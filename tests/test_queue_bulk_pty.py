import os
import select
import time

from test_picker_pty import terminal
from test_queue_store import spec

from m_dlp.queue_store import QueueStore


def wait_states(ui, expected):
    deadline = time.monotonic() + 10
    while True:
        while select.select([ui.master], [], [], 0)[0]:
            ui.data.extend(os.read(ui.master, 65536))
        with QueueStore() as store:
            actual = [row.state for row in store.snapshot()]
        if actual == expected:
            return
        assert time.monotonic() < deadline, (actual, ui.data.decode(errors="replace"))
        time.sleep(0.02)


def test_real_terminal_search_filter_bulk_controls_and_hidden_selection(tmp_path):
    with QueueStore() as writer:
        writer.add([(spec(tmp_path, number), {}, None) for number in (1, 2, 3)])
        for number, name in enumerate(("Movie One", "Movie Two", "字幕"), 1):
            writer.progress(number, name=name)
            directory = spec(tmp_path, number).destination
            directory.mkdir()
            (directory / "partial.bin").write_bytes(b"retained")
        writer.claim({3})
        writer.finish(3, "failed", error="offline")

        with terminal("queue", "tui") as ui:
            ui.read_until("任务队列")
            ui.send("/MOVIE\r \x1b[B p")
            wait_states(ui, ["paused", "paused", "failed"])
            ui.send("f" + "\x1b[B" * 4 + "\r")
            writer.progress(2, name="Other")
            ui.send("r")
            wait_states(ui, ["pending", "pending", "failed"])
            # The action refreshes the model before handling the resize, so a
            # full redraw includes both selected tasks hidden by the paused filter.
            ui.resize(12, 60)
            ui.read_until("隐藏 2")
            ui.send("c")
            wait_states(ui, ["cancelled", "cancelled", "failed"])
            ui.send("n\x1b")
            ui.resize(24, 120)
            ui.send("\x1b[Fr")
            wait_states(ui, ["cancelled", "cancelled", "pending"])
            ui.send("q")
            assert ui.finish() == 0, ui.data.decode(errors="replace")
            assert b"\x1b[?1049l" in ui.data
        for number in (1, 2, 3):
            assert (spec(tmp_path, number).destination / "partial.bin").read_bytes() == b"retained"
