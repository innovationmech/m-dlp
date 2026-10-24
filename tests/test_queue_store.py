import json
from dataclasses import replace

import pytest

from m_dlp.batch import BatchTask
from m_dlp.queue_store import QueueStore


def spec(tmp_path, number=1):
    key = f"{number:040x}"
    return BatchTask(number, number, f"magnet:?xt=urn:btih:{key}", key, tmp_path / key)


def test_store_survives_reopen_and_reserves_unfinished_destination(tmp_path):
    path = tmp_path / "state/queue.db"
    task = spec(tmp_path)
    with QueueStore(path) as store:
        first = store.add([(task, {"select": "2", "download_limit": 123}, b"torrent")])[0]
        assert store.add([(task, {}, None)])[0].id == first.id
        with pytest.raises(ValueError, match="输出目录"):
            store.add([(replace(task, key="b" * 40), {}, None)])
    with QueueStore(path) as store:
        record = store.get(first.id)
        assert record.state == "pending"
        assert record.options["select"] == "2"
        assert record.torrent == b"torrent"
        assert record.task.destination == task.destination
        assert json.loads(json.dumps(record.public()))["id"] == first.id
        assert "torrent" not in record.public()


def test_management_is_atomic_and_terminal_tasks_stay_terminal(tmp_path):
    with QueueStore(tmp_path / "queue.db") as store:
        one, two = store.add([(spec(tmp_path, n), {}, None) for n in (1, 2)])
        with pytest.raises(ValueError):
            store.control([one.id, 9999], "pause")
        assert store.get(one.id).state == "pending"
        store.control([one.id, two.id], "pause")
        assert {r.state for r in store.list()} == {"paused"}
        store.control([one.id], "resume")
        store.control([two.id], "cancel")
        assert store.get(one.id).state == "pending"
        assert store.get(two.id).state == "cancelled"
        with pytest.raises(ValueError, match="cancelled"):
            store.control([two.id], "resume")
        assert store.add([(spec(tmp_path, 2), {}, None)])[0].id != two.id


def test_runner_lock_and_crash_recovery_respect_user_intent(tmp_path):
    path = tmp_path / "queue.db"
    with QueueStore(path) as store, QueueStore(path) as other:
        records = store.add([(spec(tmp_path, n), {}, None) for n in (1, 2, 3)])
        with store.runner():
            claimed = [store.claim() for _ in records]
            assert all(r.state == "running" for r in claimed)
            other.control([records[1].id], "pause")
            other.control([records[2].id], "cancel")
            with pytest.raises(RuntimeError, match="运行"):
                with other.runner():
                    pass
            assert other.get(records[0].id).state == "running"
        # Simulate process death before it could finish/checkpoint the claims.
        with other.runner():
            assert [r.state for r in other.list()] == ["pending", "paused", "cancelled"]
            assert not any(r.active for r in other.list())


def test_resume_during_pause_cannot_claim_until_old_worker_finishes(tmp_path):
    with QueueStore(tmp_path / "queue.db") as store:
        record = store.add([(spec(tmp_path), {}, None)])[0]
        with store.runner():
            store.claim()
            store.control([record.id], "pause")
            assert store.get(record.id).state == "pausing"
            store.control([record.id], "resume")
            assert store.claim() is None
            store.finish(record.id, "cancelled")
            assert store.get(record.id).state == "pending"
            assert store.claim().id == record.id
            store.finish(record.id, "failed", error="offline")
            store.control([record.id], "resume")
            store.claim()
            store.finish(record.id, "completed")
            assert store.get(record.id).state == "completed"
            with pytest.raises(ValueError):
                store.control([record.id], "pause")
