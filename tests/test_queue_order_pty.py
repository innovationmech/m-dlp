import os
import select
import time

from test_picker_pty import terminal
from test_queue_store import spec

from m_dlp.queue_store import QueueStore


def test_real_terminal_reorder_pin_priority_and_restart(tmp_path):
    with QueueStore() as store:
        store.add([(spec(tmp_path, n), {}, None) for n in (1, 2, 3)])

    def wait_order(ui, expected):
        deadline = time.monotonic() + 10
        while True:
            while select.select([ui.master], [], [], 0)[0]:
                ui.data.extend(os.read(ui.master, 65536))
            with QueueStore() as reader:
                actual = [(r.id, r.priority, r.pinned) for r in reader.snapshot()]
            if actual == expected:
                return
            assert time.monotonic() < deadline, (actual, ui.data.decode(errors="replace"))
            time.sleep(0.02)

    with terminal("queue", "tui") as ui:
        ui.read_until("任务队列")
        ui.send("\x1b[F K+")  # selected task 3 moves up, then becomes high priority
        wait_order(ui, [(3, 2, False), (1, 1, False), (2, 1, False)])
        ui.send("n\x1b[Bt")  # pin task 1; pin outranks task 3's high priority
        wait_order(ui, [(1, 1, True), (3, 2, False), (2, 1, False)])
        ui.resize(12, 60)
        ui.read_until("顶 普通")  # pin and priority are still visible without details
        ui.send("q")
        assert ui.finish() == 0, ui.data.decode(errors="replace")
        assert b"\x1b[?1049l" in ui.data

    with terminal("queue", "tui") as ui:
        ui.read_until("顶 普通")
        ui.send("q")
        assert ui.finish() == 0
    with QueueStore() as reopened:
        assert [reopened.claim().id for _ in range(3)] == [1, 3, 2]
