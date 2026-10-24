import sqlite3

import pytest
from test_queue_store import spec

from m_dlp.queue_store import QueueStore


def ids(store):
    return [row.id for row in store.snapshot()]


def test_version_one_migrates_in_place_and_preserves_task_data(tmp_path):
    path = tmp_path / "old.db"
    # Build the actual old schema, independently of the current QueueStore constructor.
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL, key TEXT NOT NULL, destination TEXT NOT NULL,
                line INTEGER NOT NULL, options TEXT NOT NULL, torrent BLOB,
                state TEXT NOT NULL DEFAULT 'pending', active INTEGER NOT NULL DEFAULT 0,
                name TEXT NOT NULL DEFAULT '', completed INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0, rate INTEGER NOT NULL DEFAULT 0,
                peers INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            PRAGMA user_version = 1;
        """)
        for number in (3, 8):
            task = spec(tmp_path, number)
            db.execute(
                "INSERT INTO tasks(id,source,key,destination,line,options,torrent,state,"
                "completed,total,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    number,
                    task.source,
                    task.key,
                    str(task.destination),
                    number,
                    '{"select":"2"}',
                    b"saved torrent",
                    "paused",
                    12,
                    100,
                    10.0,
                    20.0,
                ),
            )
    with QueueStore(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert ids(store) == [3, 8]
        old = store.get(3)
        assert (old.priority, old.position, old.pinned) == (1, 3, False)
        assert (old.state, old.completed, old.options, old.torrent) == (
            "paused",
            12,
            {"select": "2"},
            b"saved torrent",
        )
        assert (old.created_at, old.updated_at) == (10.0, 20.0)
        assert store.add([(spec(tmp_path, 9), {}, None)])[0].id == 9
    with QueueStore(path) as reopened:
        assert ids(reopened) == [3, 8, 9]


def test_priority_pin_order_survives_reopen_and_claim_skips_ineligible(tmp_path):
    path = tmp_path / "queue.db"
    with QueueStore(path) as store:
        store.add([(spec(tmp_path, n), {}, None) for n in range(1, 6)])
        store.set_priority([2, 3], 2)
        store.set_priority([4], 0)
        store.pin([4], True)
        store.move([3], "up")
        assert ids(store) == [4, 3, 2, 1, 5]
        assert [row.id for row in store.list()] == ids(store)
        store.control([4], "pause")
        store.control([3], "cancel")
        assert store.claim().id == 2
        store.finish(2, "completed")
        public = store.get(4).public()
        assert public["priority"] == 0 and public["pinned"] is True and public["position"] > 0
    with QueueStore(path) as store:
        assert ids(store) == [4, 3, 2, 1, 5]
        assert store.claim({5}).id == 5
        assert store.claim().id == 1


def test_block_movement_and_boundaries_preserve_relative_order(tmp_path):
    with QueueStore(tmp_path / "queue.db") as store:
        store.add([(spec(tmp_path, n), {}, None) for n in range(1, 7)])
        store.move([3, 2, 5], "up")
        assert ids(store) == [2, 3, 1, 5, 4, 6]
        store.move([2, 3, 5], "down")
        assert ids(store) == [1, 2, 3, 4, 5, 6]
        store.move([2, 5], "top")
        assert ids(store) == [2, 5, 1, 3, 4, 6]
        store.move([2, 5], "bottom")
        assert ids(store) == [1, 3, 4, 6, 2, 5]
        store.set_priority([4], 2)
        assert ids(store)[0] == 4
        store.move([4], "down")  # cannot cross priority groups
        assert ids(store)[0] == 4
        store.move([1], "up")
        assert ids(store)[:2] == [4, 1]
        store.pin([3], True)
        store.move([3], "bottom")  # cannot cross pin groups
        assert ids(store)[0] == 3


def test_priority_bounds_noops_pin_toggle_and_duplicate_add(tmp_path):
    with QueueStore(tmp_path / "queue.db") as store:
        store.add([(spec(tmp_path, n), {}, None) for n in (1, 2, 3)])
        store.adjust_priority([1, 2], 1)
        store.move([2], "top")
        assert ids(store) == [2, 1, 3]
        store.adjust_priority([1, 2], 1)  # saturated tasks retain order
        assert ids(store) == [2, 1, 3]
        store.pin([3], True)
        store.pin([1, 3])  # mixed selection pins both
        assert store.get(1).pinned and store.get(3).pinned
        store.pin([1, 3])
        assert not store.get(1).pinned and not store.get(3).pinned
        before = ids(store)
        original = store.get(2)
        assert store.add([(spec(tmp_path, 2), {"changed": True}, b"new")])[0] == original
        assert ids(store) == before
        store.adjust_priority([3], -1)
        store.adjust_priority([3], -1)
        assert store.get(3).priority == 0


@pytest.mark.parametrize(
    "operation",
    [
        lambda store: store.set_priority([1, 999], 2),
        lambda store: store.adjust_priority([1, 999], 1),
        lambda store: store.pin([1, 999], True),
        lambda store: store.move([1, 999], "bottom"),
        lambda store: store.set_priority([1], 3),
        lambda store: store.move([1], "sideways"),
    ],
)
def test_invalid_reorder_is_atomic(tmp_path, operation):
    with QueueStore(tmp_path / "queue.db") as store:
        store.add([(spec(tmp_path, n), {}, None) for n in (1, 2)])
        before = store.list()
        with pytest.raises(ValueError):
            operation(store)
        assert store.list() == before
