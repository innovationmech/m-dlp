"""Exercise the real CLI in a terminal, including raw-mode restoration."""

import os
import select
import signal
import struct
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest

pty = pytest.importorskip("pty")
termios = pytest.importorskip("termios")
fcntl = pytest.importorskip("fcntl")


class Terminal:
    def __init__(self, process, master, slave):
        self.process, self.master, self.slave = process, master, slave
        self.data = bytearray()

    def read_until(self, text, timeout=20):
        expected = text.encode()
        deadline = time.monotonic() + timeout
        while expected not in self.data:
            assert time.monotonic() < deadline, self.data.decode(errors="replace")
            if select.select([self.master], [], [], 0.1)[0]:
                self.data.extend(os.read(self.master, 65536))
            elif self.process.poll() is not None:
                pytest.fail(self.data.decode(errors="replace"))

    def send(self, text):
        os.write(self.master, text.encode())

    def finish(self, timeout=30):
        deadline = time.monotonic() + timeout
        while self.process.poll() is None:
            assert time.monotonic() < deadline, self.data.decode(errors="replace")
            if select.select([self.master], [], [], 0.1)[0]:
                self.data.extend(os.read(self.master, 65536))
        while select.select([self.master], [], [], 0)[0]:
            self.data.extend(os.read(self.master, 65536))
        return self.process.returncode

    def resize(self, rows, columns):
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        self.process.send_signal(signal.SIGWINCH)


@contextmanager
def terminal(*args):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
    before = termios.tcgetattr(slave)
    process = subprocess.Popen(
        [sys.executable, "-m", "m_dlp", *args],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"},
    )
    try:
        yield Terminal(process, master, slave)
        after = termios.tcgetattr(slave)
        # macOS sets kernel-maintained PENDIN when canonical input is restored,
        # including after a direct tcsetattr(before). Compare the actual modes.
        before[3] &= ~getattr(termios, "PENDIN", 0)
        after[3] &= ~getattr(termios, "PENDIN", 0)
        assert after == before
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        os.close(master)
        os.close(slave)


def test_real_terminal_search_select_resize_then_download(seed, tmp_path):
    output = tmp_path / "downloads"
    with terminal(seed.magnet, "-o", str(output), "--metadata-timeout", "10") as ui:
        ui.read_until("选择下载文件")
        ui.read_until("已选 0/3")
        assert not output.exists()
        assert not (termios.tcgetattr(ui.slave)[3] & termios.ICANON)
        ui.resize(12, 60)
        ui.send("/second\r ")
        ui.read_until("65539 字节")
        assert not output.exists()
        ui.resize(24, 100)
        ui.send("\r")
        assert ui.finish() == 0, ui.data.decode(errors="replace")
        assert (output / "sample/second.bin").read_bytes() == seed.contents["sample/second.bin"]
        assert not (output / "sample/first.bin").exists()
        assert b"\x1b[?1049l" in ui.data  # left alternate-screen mode


@pytest.mark.parametrize("key, code", [("q", 0), ("\x03", 130), ("\x04", 130)])
def test_cancel_and_interrupt_restore_terminal_without_downloading(seed, tmp_path, key, code):
    output = tmp_path / "downloads"
    with terminal(str(seed.torrent_path), "-o", str(output)) as ui:
        ui.read_until("选择下载文件")
        ui.send(key)
        assert ui.finish() == code, ui.data.decode(errors="replace")
        assert not output.exists()
        assert b"\x1b[?1049l" in ui.data
