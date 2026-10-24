import time
from threading import Event, Lock, Thread

import pytest
from test_queue_store import spec

from m_dlp.batch import TaskEvent, TaskResult
from m_dlp.persistent_queue import run_persistent
from m_dlp.queue_store import QueueStore


def test_live_pause_resume_cancel_and_add_respect_concurrency(tmp_path):
    with QueueStore(tmp_path / "queue.db") as store, QueueStore(tmp_path / "queue.db") as control:
        records = store.add([(spec(tmp_path, n), {}, None) for n in (1, 2, 3)])
        attempts, active, peak = {}, 0, 0
        mutex = Lock()
        requested_pause = False

        def worker(record, stopped, emit):
            nonlocal active, peak
            with mutex:
                active += 1
                peak = max(peak, active)
                attempts[record.id] = attempts.get(record.id, 0) + 1
            try:
                if record.id in (1, 2) and attempts[record.id] == 1:
                    assert stopped.wait(3)
                    return TaskResult(record.task, "cancelled")
                return TaskResult(record.task, "completed")
            finally:
                with mutex:
                    active -= 1

        def observe(event):
            nonlocal requested_pause
            if event.kind == "started" and event.task.number == 1 and not requested_pause:
                requested_pause = True
                control.control([1], "pause")
            elif event.kind == "started" and event.task.number == 2:
                control.control([2], "cancel")
                control.add([(spec(tmp_path, 4), {}, None)])
            elif event.kind == "result" and event.data.state == "paused":
                control.control([1], "resume")

        report = run_persistent(store, 2, worker, observe)
        assert not report.interrupted
        assert peak <= 2
        assert attempts == {1: 2, 2: 1, 3: 1, 4: 1}
        assert [r.state for r in store.list()] == [
            "completed",
            "cancelled",
            "completed",
            "completed",
        ]
        assert len(report.results) == len(records) + 1


def test_interrupt_requeues_active_preserves_paused_and_unstarted(tmp_path):
    with QueueStore(tmp_path / "queue.db") as store:
        store.add([(spec(tmp_path, n), {}, None) for n in (1, 2, 3)])
        stop = Event()

        def worker(record, stopped, emit):
            assert stopped.wait(3)
            return TaskResult(record.task, "cancelled")

        def observe(event):
            if event.kind == "started":
                store.control([2], "pause")
                stop.set()

        report = run_persistent(store, 1, worker, observe, cancelled=stop)
        assert report.interrupted
        assert [r.state for r in store.list()] == ["pending", "paused", "pending"]
        assert not any(r.active for r in store.list())


def test_renderer_failure_joins_workers_before_releasing_runner_lock(tmp_path):
    with QueueStore(tmp_path / "queue.db") as store:
        store.add([(spec(tmp_path), {}, None)])
        closed = Event()

        def worker(record, stopped, emit):
            assert stopped.wait(3)
            closed.set()
            return TaskResult(record.task, "cancelled")

        def observe(event):
            raise RuntimeError("display failed")

        with pytest.raises(RuntimeError, match="display failed"):
            run_persistent(store, 1, worker, observe)
        assert closed.is_set()
        assert store.get(1).state == "pending"
        with store.runner():
            assert store.claim().id == 1


def test_failed_worker_does_not_block_next_and_scope_does_not_run_unrelated_tasks(tmp_path):
    with QueueStore(tmp_path / "queue.db") as store:
        store.add([(spec(tmp_path, n), {}, None) for n in (1, 2, 3)])

        def worker(record, stopped, emit):
            if record.id == 1:
                raise RuntimeError("offline")
            emit(TaskEvent(record.task, "resolved", ("sample", 42)))
            return TaskResult(record.task, "completed", "sample")

        report = run_persistent(store, 1, worker, lambda event: None, ids={1, 2})
        assert [r.state for r in report.results] == ["failed", "completed"]
        assert store.get(1).error == "offline"
        assert store.get(2).total == 42
        assert store.get(3).state == "pending"


def test_live_reorder_applies_at_next_slot_without_preempting_or_expanding_scope(tmp_path):
    path = tmp_path / "queue.db"
    release = Event()
    started = []
    with QueueStore(path) as store, QueueStore(path) as control:
        store.add([(spec(tmp_path, n), {}, None) for n in range(1, 6)])
        control.pin([5], True)  # unrelated task must stay pending even when pinned

        def worker(record, stopped, emit):
            if record.id == 1:
                assert release.wait(3)
                assert not stopped.is_set()
            return TaskResult(record.task, "completed")

        def observe(event):
            if event.kind == "started":
                started.append(event.task.number)
                if event.task.number == 1:
                    control.set_priority([4], 2)
                    control.pin([3], True)
                    assert control.get(1).active and control.get(1).state == "running"
                    release.set()

        report = run_persistent(store, 1, worker, observe, ids={1, 2, 3, 4})
        assert started == [1, 3, 4, 2]
        assert all(result.state == "completed" for result in report.results)
        assert control.get(5).state == "pending"


def test_keep_alive_resumes_after_idle_and_accepts_new_tasks(tmp_path):
    path = tmp_path / "queue.db"
    stop, started, finished = Event(), Event(), Event()
    errors = []

    def worker(record, stopped, emit):
        started.set()
        assert stopped.wait(3)
        return TaskResult(record.task, "cancelled")

    def run():
        try:
            with QueueStore(path) as store:
                run_persistent(store, 1, worker, lambda e: None, cancelled=stop, keep_alive=True)
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    with QueueStore(path) as control:
        thread = Thread(target=run)
        thread.start()

        def until(predicate):
            deadline = time.monotonic() + 3
            while not predicate():
                assert not errors, errors
                assert time.monotonic() < deadline
                time.sleep(0.01)

        try:
            # A runner started with an empty queue stays ready for added tasks.
            assert not finished.wait(0.2)
            control.add([(spec(tmp_path), {}, None)])
            assert started.wait(3)
            control.control([1], "pause")
            until(lambda: control.get(1).state == "paused")
            started.clear()
            assert not finished.wait(0.2)
            control.control([1], "resume")
            assert started.wait(3)
        finally:
            stop.set()
            thread.join(5)
        assert not thread.is_alive()
        assert not errors
        assert control.get(1).state == "pending"
        assert not control.get(1).active
