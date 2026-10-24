"""Validated batch input and bounded scheduling, independent of terminal rendering."""

import signal
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event, current_thread, main_thread

import libtorrent as lt

from .cache import hash_keys


@dataclass(frozen=True)
class BatchTask:
    number: int
    line: int
    source: str
    key: str
    destination: Path


@dataclass(frozen=True)
class BatchInput:
    tasks: tuple[BatchTask, ...]
    duplicates: int


@dataclass(frozen=True)
class TaskResult:
    task: BatchTask
    state: str
    name: str = ""
    error: str = ""
    warnings: tuple[str, ...] = ()
    files: tuple = ()
    diagnostics: object = None


@dataclass(frozen=True)
class TaskEvent:
    task: BatchTask
    kind: str
    data: object = None


@dataclass(frozen=True)
class QueueReport:
    results: tuple[TaskResult, ...]
    interrupted: bool


@contextmanager
def cancel_on_sigint(cancelled: Event):
    """Signal cancellation without interrupting an event/UI state update."""
    if current_thread() is not main_thread():
        yield
        return
    previous = signal.signal(signal.SIGINT, lambda *_: cancelled.set())
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def load_batch(path: Path, output: Path) -> BatchInput:
    lines = path.expanduser().read_text(encoding="utf-8-sig").splitlines()
    records = []
    parents = []
    owners = {}

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for line, value in enumerate(lines, 1):
        source = value.strip()
        if not source or source.startswith("#"):
            continue
        try:
            if not source.startswith("magnet:?"):
                raise ValueError("请输入磁力链接，每行一个")
            keys = hash_keys(lt.parse_magnet_uri(source).info_hashes)
            if not keys:
                raise ValueError("缺少 info hash")
        except (ValueError, RuntimeError) as exc:
            # Do not echo URLs containing tracker credentials into diagnostics.
            raise ValueError(f"批量文件第 {line} 行不是有效的磁力链接。") from exc
        index = len(records)
        records.append((line, source, keys))
        parents.append(index)
        for key in keys:
            if key in owners:
                a, b = root(index), root(owners[key])
                parents[max(a, b)] = min(a, b)
            owners[key] = index
    if not records:
        raise ValueError("批量文件没有可用的磁力链接。")

    # A later hybrid magnet can reveal that two earlier v1/v2 aliases are the
    # same resource. Group after the whole file has been validated.
    groups = {}
    for index, (_, _, keys) in enumerate(records):
        groups.setdefault(root(index), set()).update(keys)
    output = output.expanduser().absolute()
    tasks = []
    for index, keys in groups.items():
        line, source, _ = records[index]
        key = sorted(keys, key=lambda key: (-len(key), key))[0]
        tasks.append(BatchTask(len(tasks) + 1, line, source, key, output / key))
    return BatchInput(tuple(tasks), len(records) - len(tasks))


def run_queue(
    tasks: Sequence[BatchTask],
    jobs: int,
    worker: Callable,
    on_event: Callable[[TaskEvent], None],
    *,
    cancelled: Event | None = None,
) -> QueueReport:
    if type(jobs) is not int or jobs < 1:
        raise ValueError("同时运行的任务数必须是正整数。")
    cancelled = cancelled if cancelled is not None else Event()
    events = SimpleQueue()
    results = {}
    active = {}
    next_task = 0
    executor = ThreadPoolExecutor(max_workers=min(jobs, len(tasks)) or 1)

    def drain():
        while True:
            try:
                event = events.get_nowait()
            except Empty:
                return
            on_event(event)

    try:
        while active or (next_task < len(tasks) and not cancelled.is_set()):
            try:
                while len(active) < jobs and next_task < len(tasks) and not cancelled.is_set():
                    task = tasks[next_task]
                    next_task += 1
                    events.put(TaskEvent(task, "started"))
                    active[executor.submit(worker, task, cancelled, events.put)] = task
                drain()
                done, _ = wait(active, timeout=0.1, return_when=FIRST_COMPLETED)
                # Consume each worker's last progress event before its result.
                drain()
                for future in done:
                    task = active.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = TaskResult(task, "failed", error=str(exc))
                    except KeyboardInterrupt:
                        cancelled.set()
                        result = TaskResult(task, "cancelled")
                    results[task.number] = result
                    on_event(TaskEvent(task, "result", result))
            except KeyboardInterrupt:
                cancelled.set()
        drain()
    finally:
        # Also unwind workers if rendering or other main-thread work fails.
        if active:
            cancelled.set()
        executor.shutdown(wait=True, cancel_futures=True)
    return QueueReport(
        tuple(results.get(task.number, TaskResult(task, "pending")) for task in tasks),
        cancelled.is_set(),
    )
