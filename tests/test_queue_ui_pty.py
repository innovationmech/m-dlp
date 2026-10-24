import os
import select
import signal
import subprocess
import sys
import time

import pytest
from test_cli import invoke
from test_picker_pty import pty, terminal
from test_queue_cli import MAGNET

from m_dlp.queue_store import QueueStore


@pytest.mark.parametrize("environment", ["FORCE_COLOR", "TTY_COMPATIBLE"])
@pytest.mark.parametrize("command", [("tui",), ("run", "--interactive")])
def test_redirected_output_rejected_even_when_rich_forces_color(tmp_path, environment, command):
    master, slave = pty.openpty()
    database = tmp_path / "must-not-create.db"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "m_dlp", "queue", *command, "--queue-db", str(database)],
            stdin=slave,
            capture_output=True,
            text=True,
            timeout=3,
            env={**os.environ, "TERM": "xterm-256color", environment: "1"},
        )
        assert result.returncode == 2
        assert "需要可交互终端" in result.stderr
        assert not result.stdout
        assert not database.exists()
    finally:
        os.close(master)
        os.close(slave)


def wait_task(*terminals, states=None, progressing=False, timeout=15):
    # A terminal emulator continuously drains output. Without this, the macOS
    # PTY buffer can fill during live redraws and block the UI's input loop.
    deadline = time.monotonic() + timeout
    while True:
        for ui in terminals:
            while select.select([ui.master], [], [], 0)[0]:
                ui.data.extend(os.read(ui.master, 65536))
        with QueueStore() as store:
            record = store.get(1)
        if (progressing and 0 < record.completed < record.total) or (
            states is not None and record.state in states
        ):
            return record
        assert time.monotonic() < deadline, (
            record,
            [u.data.decode(errors="replace") for u in terminals],
        )
        time.sleep(0.02)


def test_interactive_runner_download_pause_resume_and_idle_exit(seed, tmp_path):
    result = invoke(
        "queue",
        "add",
        seed.magnet,
        "--select",
        "2",
        "--download-limit",
        "8K",
        "--metadata-timeout",
        "15",
        "-o",
        str(tmp_path / "out"),
    )
    assert result.returncode == 0, result.stderr
    with terminal("queue", "run", "--interactive", "-j", "1") as ui:
        ui.read_until("任务队列")
        ui.read_until("交互运行")
        wait_task(ui, progressing=True, timeout=25)
        ui.read_until("sample")  # name arrived from the live worker
        ui.resize(12, 60)
        ui.send("p")
        wait_task(ui, states={"paused"})
        assert ui.process.poll() is None
        ui.resize(24, 120)
        ui.send("r")
        record = wait_task(ui, states={"completed"}, timeout=40)
        assert (record.task.destination / "sample/second.bin").read_bytes() == (
            seed.contents["sample/second.bin"]
        )
        assert not (record.task.destination / "sample/first.bin").exists()
        assert ui.process.poll() is None  # stays ready for newly added tasks
        ui.send("q")
        assert ui.finish() == 0, ui.data.decode(errors="replace")
        assert b"\x1b[?1049l" in ui.data


def test_monitor_controls_another_runner_and_quit_leaves_it_running(tmp_path):
    assert invoke("queue", "add", MAGNET, "--all", "-o", str(tmp_path / "out")).returncode == 0
    with terminal("queue", "run", "--interactive") as runner:
        runner.read_until("任务队列")
        wait_task(runner, states={"running"})
        with terminal("queue", "tui") as monitor:
            monitor.read_until("管理模式")
            monitor.send("p")
            wait_task(runner, monitor, states={"paused"})
            monitor.send("r")
            wait_task(runner, monitor, states={"running"})
            monitor.send("q")
            assert monitor.finish() == 0
        assert runner.process.poll() is None
        assert wait_task(runner, states={"running"}).active
        runner.send("c")
        wait_task(runner, states={"cancelled"})
        runner.send("q")
        assert runner.finish() == 0


@pytest.mark.parametrize("key", ["q", "\x03", "\x04", "signal"])
def test_queue_exit_restores_terminal_and_requeues_active_download(tmp_path, key):
    assert invoke("queue", "add", MAGNET, "--all", "-o", str(tmp_path / "out")).returncode == 0
    with terminal("queue", "run", "--interactive") as ui:
        ui.read_until("任务队列")
        wait_task(ui, states={"running"})
        if key == "signal":
            ui.process.send_signal(signal.SIGINT)
        else:
            ui.send(key)
        assert ui.finish() == (0 if key == "q" else 130), ui.data.decode(errors="replace")
        assert b"\x1b[?1049l" in ui.data
    with QueueStore() as store:
        assert store.get(1).state == "pending"
        assert not store.get(1).active
        with store.runner():
            pass
