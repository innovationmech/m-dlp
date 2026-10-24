import threading

import pytest

from m_dlp import batch


def magnet(n):
    return f"magnet:?xt=urn:btih:{n:040x}"


def tasks(tmp_path, count=5):
    path = tmp_path / "links.txt"
    path.write_text("\n".join(magnet(n + 1) for n in range(count)))
    return batch.load_batch(path, tmp_path / "downloads").tasks


def test_batch_reads_bom_comments_deduplicates_and_stable_paths(tmp_path):
    path = tmp_path / "links.txt"
    path.write_text(f"\ufeff# links\n\n {magnet(1)} \n{magnet(2)}\n{magnet(1)}&dn=alias\n")
    loaded = batch.load_batch(path, tmp_path / "downloads")
    assert len(loaded.tasks) == 2
    assert loaded.duplicates == 1
    assert [t.line for t in loaded.tasks] == [3, 4]
    assert loaded.tasks[0].destination == tmp_path / "downloads" / f"{1:040x}"
    path.write_text(f"{magnet(2)}\n{magnet(1)}")
    reordered = batch.load_batch(path, tmp_path / "downloads")
    assert reordered.tasks[1].destination == loaded.tasks[0].destination
    assert not (tmp_path / "downloads").exists()


def test_hybrid_alias_connects_previously_separate_entries(tmp_path):
    path = tmp_path / "links.txt"
    v2 = "magnet:?xt=urn:btmh:1220" + "b" * 64
    path.write_text(f"{magnet(1)}\n{v2}\n{magnet(1)}&xt=urn:btmh:1220{'b' * 64}\n")
    loaded = batch.load_batch(path, tmp_path / "downloads")
    assert len(loaded.tasks) == 1
    assert loaded.duplicates == 2


@pytest.mark.parametrize("text", ["", "# only comment\n", "magnet:?dn=missing", "file.torrent"])
def test_bad_batch_rejected(tmp_path, text):
    path = tmp_path / "links.txt"
    path.write_text(text)
    with pytest.raises(ValueError):
        batch.load_batch(path, tmp_path / "downloads")


def test_invalid_line_reports_number_without_echoing_credentials(tmp_path):
    path = tmp_path / "links.txt"
    path.write_text(f"{magnet(1)}\nhttps://host/secret-key\n")
    with pytest.raises(ValueError, match="第 2 行") as exc:
        batch.load_batch(path, tmp_path / "downloads")
    assert "secret-key" not in str(exc.value)


def test_queue_bounds_concurrency_and_continues_after_error(tmp_path):
    items = tasks(tmp_path)
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=3)
    active = peak = 0
    dispatch = []

    def worker(task, cancelled, emit):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            if task.number <= 2:
                barrier.wait()
            if task.number == 1:
                raise ValueError("one resource failed")
            return batch.TaskResult(task, "completed")
        finally:
            with lock:
                active -= 1

    def observe(event):
        if event.kind == "started":
            dispatch.append(event.task.number)

    report = batch.run_queue(items, 2, worker, observe)
    assert peak == 2
    assert active == 0
    assert dispatch == [1, 2, 3, 4, 5]
    assert [r.state for r in report.results] == ["failed"] + ["completed"] * 4
    assert report.results[0].error == "one resource failed"
    assert not report.interrupted


def test_interrupt_cancels_active_workers_without_starting_pending(tmp_path):
    items = tasks(tmp_path)
    visited = []

    def worker(task, cancelled, emit):
        visited.append(task.number)
        assert cancelled.wait(3)
        return batch.TaskResult(task, "cancelled")

    def interrupt(event):
        if event.kind == "started" and event.task.number == 1:
            raise KeyboardInterrupt

    report = batch.run_queue(items, 2, worker, interrupt)
    assert report.interrupted
    assert set(visited) <= {1, 2}
    assert all(r.state in {"cancelled", "pending"} for r in report.results)
    assert [r.state for r in report.results[2:]] == ["pending"] * 3


def test_presentation_error_also_cancels_workers(tmp_path):
    stopped = threading.Event()

    def worker(task, cancelled, emit):
        assert cancelled.wait(3)
        stopped.set()
        return batch.TaskResult(task, "cancelled")

    def broken_display(event):
        raise RuntimeError("display failure")

    with pytest.raises(RuntimeError, match="display failure"):
        batch.run_queue(tasks(tmp_path), 1, worker, broken_display)
    assert stopped.is_set()


def test_pre_cancelled_queue_does_not_call_worker(tmp_path):
    cancelled = threading.Event()
    cancelled.set()

    def worker(*args):
        pytest.fail("must not start a task")

    report = batch.run_queue(tasks(tmp_path), 3, worker, lambda event: None, cancelled=cancelled)
    assert report.interrupted
    assert all(r.state == "pending" for r in report.results)
