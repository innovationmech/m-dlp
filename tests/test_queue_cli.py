import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_cli import invoke

from m_dlp.queue_store import QueueStore

MAGNET = "magnet:?xt=urn:btih:" + "a" * 40


def records():
    result = invoke("queue", "list", "--json")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def wait_state(task_id, expected, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with QueueStore() as store:
            record = store.get(task_id)
        if record.state in expected:
            return record
        time.sleep(0.05)
    pytest.fail(f"Task {task_id}: wanted {expected}, got {record.state}")


def launch(*args):
    return subprocess.Popen(
        [sys.executable, "-m", "m_dlp", *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def stop(process):
    if process.poll() is None:
        process.kill()
    return process.communicate(timeout=15)


def test_queue_add_list_show_control_are_durable_and_do_not_download(tmp_path):
    output = tmp_path / "downloads"
    result = invoke("queue", "add", MAGNET, "--all", "-o", str(output), "--download-limit", "2M")
    assert result.returncode == 0, result.stderr
    task = records()[0]
    assert task["state"] == "pending"
    assert task["options"]["download_limit"] == 2 * 1024**2
    assert not output.exists()
    assert not (tmp_path / "cache").exists()
    result = invoke("queue", "show", str(task["id"]), "--json")
    assert json.loads(result.stdout)["destination"] == task["destination"]
    for action, state in [("pause", "paused"), ("resume", "pending"), ("cancel", "cancelled")]:
        result = invoke("queue", action, str(task["id"]))
        assert result.returncode == 0, result.stderr
        assert records()[0]["state"] == state
    assert invoke("queue", "resume", str(task["id"])).returncode == 2
    assert invoke("queue", "show", "9999").returncode == 2


@pytest.mark.parametrize("extra", [[], ["--list"], ["--diagnose"], ["--export-torrent", "out"]])
def test_queue_add_requires_download_selection(extra):
    assert invoke("queue", "add", MAGNET, *extra).returncode == 2
    assert not Path(os.environ["M_DLP_QUEUE_DB"]).exists()


def test_queue_database_config_precedence_and_empty_queue(tmp_path):
    config = tmp_path / "settings.toml"
    config.write_text('queue-db = "configured/state.db"\njobs = 1\n')
    added = invoke("queue", "add", MAGNET, "--all", "--config", str(config))
    assert added.returncode == 0, added.stderr
    assert (tmp_path / "configured/state.db").exists()
    assert len(json.loads(invoke("queue", "list", "--config", str(config), "--json").stdout)) == 1
    assert records() == []  # environment store is separate
    empty = invoke("queue", "run", "--queue-db", str(tmp_path / "empty.db"))
    assert empty.returncode == 0, empty.stderr


def test_torrent_snapshot_survives_input_removal_and_downloads_selected_files(seed, tmp_path):
    output = tmp_path / "downloads"
    # Existing payload lets the immutable snapshot be verified offline.
    added = invoke("queue", "add", str(seed.torrent_path), "--select", "2", "-o", str(output))
    assert added.returncode == 0, added.stderr
    task = records()[0]
    destination = Path(task["destination"])
    for name, data in seed.contents.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    seed.torrent_path.unlink()
    seed.session.pause()
    result = invoke("queue", "run", "-j", "1", timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)
    task = records()[0]
    assert task["state"] == "completed"
    assert task["completed"] == task["total"] == len(seed.contents["sample/second.bin"])


def test_other_terminal_controls_live_worker_and_recovers_after_kill(tmp_path):
    assert invoke("queue", "add", MAGNET, "--all").returncode == 0
    process = launch("queue", "run", "-j", "1")
    try:
        wait_state(1, {"running"})
        second = invoke("queue", "run")
        assert second.returncode == 1 and "已有下载进程" in second.stderr
        assert invoke("queue", "pause", "1").returncode == 0
        wait_state(1, {"paused"})
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, (stdout, stderr)
    finally:
        stop(process)
    assert invoke("queue", "resume", "1").returncode == 0
    process = launch("queue", "run")
    try:
        wait_state(1, {"running"})
        process.kill()
        process.communicate(timeout=15)
    finally:
        stop(process)
    # No manual database editing or stale-lock deletion after abrupt death.
    process = launch("queue", "run")
    try:
        # claim was already running before the kill; wait for a new timestamp.
        time.sleep(0.4)
        assert process.poll() is None
        assert invoke("queue", "cancel", "1").returncode == 0
        wait_state(1, {"cancelled"})
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, (stdout, stderr)
    finally:
        stop(process)


def test_batch_sigint_is_saved_and_can_restart_without_input_file(tmp_path):
    batch = tmp_path / "links.txt"
    batch.write_text(MAGNET + "\n" + MAGNET.replace("a" * 40, "b" * 40))
    process = launch("-i", str(batch), "--all", "-j", "1")
    try:
        deadline = time.monotonic() + 10
        while not Path(os.environ["M_DLP_QUEUE_DB"]).exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        wait_state(1, {"running"})
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 130, (stdout, stderr)
        assert [r["state"] for r in records()] == ["pending", "pending"]
        batch.unlink()
        assert invoke("queue", "pause", "1", "2").returncode == 0
        assert invoke("queue", "run").returncode == 0
    finally:
        stop(process)


def test_pause_payload_checkpoint_and_resume_to_exact_files(seed, tmp_path):
    added = invoke(
        "queue",
        "add",
        seed.magnet,
        "--all",
        "--download-limit",
        "8K",
        "--metadata-timeout",
        "15",
        "-o",
        str(tmp_path / "downloads"),
    )
    assert added.returncode == 0, added.stderr
    process = launch("queue", "run", "-j", "1")
    try:
        deadline = time.monotonic() + 25
        while True:
            with QueueStore() as store:
                task = store.get(1)
            if 0 < task.completed < task.total:
                break
            assert task.state not in {"completed", "failed"}, task
            assert time.monotonic() < deadline, task
            time.sleep(0.1)
        assert invoke("queue", "pause", "1").returncode == 0
        paused = wait_state(1, {"paused"})
        process.communicate(timeout=15)
        assert process.returncode == 0
        assert not paused.active and paused.rate == 0
        assert list((tmp_path / "cache/resume").glob("*.json"))
        assert invoke("queue", "resume", "1").returncode == 0
        result = invoke("queue", "run", "-j", "1", timeout=40)
        assert result.returncode == 0, (result.stdout, result.stderr)
        final = records()[0]
        assert final["state"] == "completed"
        for name, data in seed.contents.items():
            assert (Path(final["destination"]) / name).read_bytes() == data
    finally:
        stop(process)
