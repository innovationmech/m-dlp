"""Durable task records and cross-process management, independent of downloads."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .batch import BatchTask

STATES = (
    "pending",
    "running",
    "pausing",
    "paused",
    "cancelling",
    "cancelled",
    "completed",
    "failed",
)
PRIORITIES = {"low": 0, "normal": 1, "high": 2}
QUEUE_ORDER = "pinned DESC, priority DESC, position ASC, id ASC"


def default_queue_path() -> Path:
    if value := os.environ.get("M_DLP_QUEUE_DB"):
        return Path(value)
    if value := os.environ.get("XDG_DATA_HOME"):
        return Path(value) / "m-dlp/queue.sqlite3"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/m-dlp/queue.sqlite3"
    if sys.platform == "win32" and (value := os.environ.get("LOCALAPPDATA")):
        return Path(value) / "m-dlp/queue.sqlite3"
    return Path.home() / ".local/share/m-dlp/queue.sqlite3"


@dataclass(frozen=True)
class StoredTask:
    id: int
    task: BatchTask
    options: dict
    torrent: bytes | None
    state: str
    active: bool
    name: str
    completed: int
    total: int
    rate: int
    peers: int
    error: str
    created_at: float
    updated_at: float
    priority: int = 1
    position: int = 0
    pinned: bool = False

    def public(self) -> dict:
        return {
            "id": self.id,
            "state": self.state,
            "active": self.active,
            "name": self.name,
            "source": self.task.source,
            "key": self.task.key,
            "destination": str(self.task.destination),
            "line": self.task.line,
            "completed": self.completed,
            "total": self.total,
            "rate": self.rate,
            "peers": self.peers,
            "error": self.error,
            "options": self.options,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "priority": self.priority,
            "position": self.position,
            "pinned": self.pinned,
        }


@dataclass(frozen=True)
class TaskSnapshot:
    id: int
    state: str
    active: int
    name: str
    key: str
    destination: str
    completed: int
    total: int
    rate: int
    peers: int
    error: str
    priority: int = 1
    position: int = 0
    pinned: bool = False


class QueueStore:
    def __init__(self, path: Path | None = None, *, timeout=5):
        self.path = (path or default_queue_path()).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(self.path, timeout=timeout)
        self.db.row_factory = sqlite3.Row
        try:
            with self.transaction():
                version = self.db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1, 2):
                    raise ValueError(f"不支持的队列数据库版本：{version}")
                self.db.execute("""
                    CREATE TABLE IF NOT EXISTS tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source TEXT NOT NULL, key TEXT NOT NULL, destination TEXT NOT NULL,
                        line INTEGER NOT NULL, options TEXT NOT NULL, torrent BLOB,
                        state TEXT NOT NULL DEFAULT 'pending', active INTEGER NOT NULL DEFAULT 0,
                        name TEXT NOT NULL DEFAULT '', completed INTEGER NOT NULL DEFAULT 0,
                        total INTEGER NOT NULL DEFAULT 0, rate INTEGER NOT NULL DEFAULT 0,
                        peers INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
                        created_at REAL NOT NULL, updated_at REAL NOT NULL
                    )
                """)
                self.db.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS reserved_destination ON tasks(destination)
                    WHERE state NOT IN ('completed', 'cancelled') OR active = 1
                """)
                if version < 2:
                    self.db.execute(
                        "ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 1 "
                        "CHECK(priority BETWEEN 0 AND 2)"
                    )
                    self.db.execute(
                        "ALTER TABLE tasks ADD COLUMN position INTEGER NOT NULL DEFAULT 0"
                    )
                    self.db.execute(
                        "ALTER TABLE tasks ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0 "
                        "CHECK(pinned IN (0, 1))"
                    )
                    self.db.execute("UPDATE tasks SET position = id")
                    self.db.execute("PRAGMA user_version = 2")
                self.db.execute(
                    "CREATE INDEX IF NOT EXISTS queue_dispatch ON tasks "
                    "(state, active, pinned DESC, priority DESC, position, id)"
                )
        except BaseException:
            self.db.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        with self.db:
            yield

    @staticmethod
    def _record(row) -> StoredTask:
        return StoredTask(
            row["id"],
            BatchTask(row["id"], row["line"], row["source"], row["key"], Path(row["destination"])),
            json.loads(row["options"]),
            row["torrent"],
            row["state"],
            bool(row["active"]),
            row["name"],
            row["completed"],
            row["total"],
            row["rate"],
            row["peers"],
            row["error"],
            row["created_at"],
            row["updated_at"],
            row["priority"],
            row["position"],
            bool(row["pinned"]),
        )

    def get(self, task_id: int) -> StoredTask:
        row = self.db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"任务 {task_id} 不存在。")
        return self._record(row)

    def list(self) -> list[StoredTask]:
        return [
            self._record(row)
            for row in self.db.execute(f"SELECT * FROM tasks ORDER BY {QUEUE_ORDER}")
        ]

    def snapshot(self) -> list[TaskSnapshot]:
        """Small live rows for polling; never fetch source/configuration/torrent blobs."""
        rows = self.db.execute(
            "SELECT id, state, active, name, key, destination, completed, total, "
            f"rate, peers, error, priority, position, pinned FROM tasks ORDER BY {QUEUE_ORDER}"
        )
        return [TaskSnapshot(**{**row, "pinned": bool(row["pinned"])}) for row in rows]

    def add(self, specs) -> list[StoredTask]:
        ids = []
        with self.transaction():
            position = self.db.execute("SELECT COALESCE(MAX(position), 0) FROM tasks").fetchone()[0]
            for task, options, torrent in specs:
                # Resolve the user-controlled root but retain the final component
                # so the worker can still reject a generated directory symlink.
                path = task.destination.expanduser().absolute()
                destination = str(path.parent.resolve() / path.name)
                row = self.db.execute(
                    "SELECT id, key FROM tasks WHERE destination = ? "
                    "AND (state NOT IN ('completed', 'cancelled') OR active = 1)",
                    (destination,),
                ).fetchone()
                if row:
                    if row["key"] != task.key:
                        raise ValueError(f"输出目录已被任务 {row['id']} 占用。")
                    ids.append(row["id"])
                    continue
                now = time.time()
                position += 1
                cursor = self.db.execute(
                    "INSERT INTO tasks(source, key, destination, line, options, torrent, "
                    "created_at, updated_at, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        task.source,
                        task.key,
                        destination,
                        task.line,
                        json.dumps(options),
                        torrent,
                        now,
                        now,
                        position,
                    ),
                )
                ids.append(cursor.lastrowid)
        return [self.get(task_id) for task_id in dict.fromkeys(ids)]

    def _ordering(self, ids):
        """Read/validate inside the caller's transaction without fetching torrent blobs."""
        rows = list(
            self.db.execute(
                f"SELECT id, priority, pinned, position FROM tasks ORDER BY {QUEUE_ORDER}"
            )
        )
        selected = set(ids)
        missing = selected - {row["id"] for row in rows}
        if missing:
            raise ValueError(f"任务 {min(missing)} 不存在。")
        return rows, selected

    def _reposition(self, ids):
        now = time.time()
        self.db.executemany(
            "UPDATE tasks SET position = ?, updated_at = ? WHERE id = ? AND position != ?",
            ((position, now, task_id, position) for position, task_id in enumerate(ids, 1)),
        )

    def _change_priorities(self, ids, choose):
        with self.transaction():
            rows, selected = self._ordering(ids)
            changes = {
                row["id"]: choose(row["priority"])
                for row in rows
                if row["id"] in selected and choose(row["priority"]) != row["priority"]
            }
            if changes:
                now = time.time()
                self.db.executemany(
                    "UPDATE tasks SET priority = ?, updated_at = ? WHERE id = ?",
                    ((value, now, task_id) for task_id, value in changes.items()),
                )
                # Changed tasks join the tail of their new scheduling group.
                order = [row["id"] for row in rows if row["id"] not in changes]
                order += [row["id"] for row in rows if row["id"] in changes]
                self._reposition(order)
        return [self.get(task_id) for task_id in dict.fromkeys(ids)]

    def set_priority(self, ids, priority):
        if type(priority) is not int or priority not in PRIORITIES.values():
            raise ValueError("优先级必须是 0（低）、1（普通）或 2（高）。")
        return self._change_priorities(ids, lambda old: priority)

    def adjust_priority(self, ids, delta):
        if type(delta) is not int or delta not in (-1, 1):
            raise ValueError("优先级调整必须是 +1 或 -1。")
        return self._change_priorities(ids, lambda old: max(0, min(2, old + delta)))

    def pin(self, ids, pinned=None):
        if pinned is not None and type(pinned) is not bool:
            raise ValueError("置顶状态必须是布尔值。")
        with self.transaction():
            rows, selected = self._ordering(ids)
            targets = [row["id"] for row in rows if row["id"] in selected]
            others = [row["id"] for row in rows if row["id"] not in selected]
            if pinned is None:
                pinned = not all(row["pinned"] for row in rows if row["id"] in selected)
            now = time.time()
            self.db.executemany(
                "UPDATE tasks SET pinned = ?, updated_at = ? WHERE id = ?",
                ((int(pinned), now, task_id) for task_id in targets),
            )
            if targets:
                self._reposition(targets + others if pinned else others + targets)
        return [self.get(task_id) for task_id in dict.fromkeys(ids)]

    def move(self, ids, direction):
        if direction not in {"up", "down", "top", "bottom"}:
            raise ValueError("移动方向必须是 up、down、top 或 bottom。")
        with self.transaction():
            rows, selected = self._ordering(ids)
            groups = {}
            for row in rows:
                groups.setdefault((row["pinned"], row["priority"]), []).append(row["id"])
            order = []
            for group in groups.values():
                if direction in {"top", "bottom"}:
                    targets = [task_id for task_id in group if task_id in selected]
                    others = [task_id for task_id in group if task_id not in selected]
                    group = targets + others if direction == "top" else others + targets
                else:
                    # Walking in the direction opposite to movement shifts each
                    # selected block once and preserves relative selected order.
                    indices = (
                        range(1, len(group)) if direction == "up" else range(len(group) - 2, -1, -1)
                    )
                    step = -1 if direction == "up" else 1
                    for index in indices:
                        neighbor = index + step
                        if group[index] in selected and group[neighbor] not in selected:
                            group[index], group[neighbor] = group[neighbor], group[index]
                order.extend(group)
            if order != [row["id"] for row in rows]:
                self._reposition(order)
        return [self.get(task_id) for task_id in dict.fromkeys(ids)]

    def control(self, ids: list[int], action: str) -> list[StoredTask]:
        with self.transaction():
            for task_id in dict.fromkeys(ids):
                task = self.get(task_id)
                state = task.state
                if action == "pause" and state in ("pending", "running", "pausing", "paused"):
                    state = "pausing" if task.active else "paused"
                elif action == "resume" and state in (
                    "pending",
                    "running",
                    "pausing",
                    "paused",
                    "failed",
                ):
                    state = "running" if state == "running" else "pending"
                elif action == "cancel" and state != "completed":
                    state = "cancelling" if task.active else "cancelled"
                else:
                    raise ValueError(f"任务 {task_id} 的状态 {state} 不支持 {action}。")
                self.db.execute(
                    "UPDATE tasks SET state = ?, rate = 0, peers = 0, updated_at = ? WHERE id = ?",
                    (state, time.time(), task_id),
                )
        return [self.get(task_id) for task_id in dict.fromkeys(ids)]

    @contextmanager
    def runner(self):
        """OS lock has no timeout: a slow/live runner can never be stolen."""
        lock_path = self.path.with_name(self.path.name + ".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "r+b") as lock:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    if lock_path.stat().st_size == 0:
                        lock.write(b"\0")
                        lock.flush()
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("该队列已有下载进程运行；可在另一个终端管理任务。") from exc
            try:
                self.recover()
                yield
            finally:
                if sys.platform == "win32":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def recover(self):
        # Caller holds the runner lock and has joined any workers in this process.
        with self.transaction():
            self.db.execute(
                "UPDATE tasks SET state = CASE state WHEN 'pausing' THEN 'paused' "
                "WHEN 'cancelling' THEN 'cancelled' ELSE 'pending' END, "
                "active = 0, rate = 0, peers = 0, updated_at = ? WHERE active = 1",
                (time.time(),),
            )

    def claim(self, ids: set[int] | None = None) -> StoredTask | None:
        with self.transaction():
            # Filtering in Python avoids SQLite's variable limit for large input files.
            row = next(
                (
                    row
                    for row in self.db.execute(
                        "SELECT id FROM tasks WHERE state = 'pending' AND active = 0 "
                        f"ORDER BY {QUEUE_ORDER}"
                    )
                    if ids is None or row["id"] in ids
                ),
                None,
            )
            if row is None:
                return None
            self.db.execute(
                "UPDATE tasks SET state = 'running', active = 1, error = '', "
                "updated_at = ? WHERE id = ?",
                (time.time(), row["id"]),
            )
            return self.get(row["id"])

    def progress(
        self, task_id: int, *, name=None, completed=None, total=None, rate=None, peers=None
    ):
        values = {
            "name": name,
            "completed": completed,
            "total": total,
            "rate": rate,
            "peers": peers,
            "updated_at": time.time(),
        }
        values = {key: value for key, value in values.items() if value is not None}
        with self.transaction():
            self.db.execute(
                f"UPDATE tasks SET {', '.join(f'{key} = ?' for key in values)} WHERE id = ?",
                (*values.values(), task_id),
            )

    def finish(self, task_id: int, result: str, *, error="", interrupted=False) -> StoredTask:
        with self.transaction():
            task = self.get(task_id)
            if task.state == "cancelling":
                state = "cancelled"
            elif task.state == "pausing":
                state = "paused"
            elif task.state == "pending":
                state = "pending"  # resume arrived while the old worker was closing
            elif result == "completed":
                state = "completed"
            elif interrupted or result == "cancelled":
                state = "pending"
            else:
                state = "failed"
            self.db.execute(
                "UPDATE tasks SET state = ?, active = 0, rate = 0, peers = 0, error = ?, "
                "updated_at = ? WHERE id = ?",
                (state, error if state == "failed" else "", time.time(), task_id),
            )
        return self.get(task_id)
