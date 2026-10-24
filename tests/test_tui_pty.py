from test_picker_pty import terminal
from test_queue_store import spec

from m_dlp.queue_store import QueueStore


def test_queue_reading_views_resize_and_return_without_changing_tasks(tmp_path):
    with QueueStore() as store:
        store.add([(spec(tmp_path), {}, None)])
        store.progress(1, name="电影合辑", completed=50, total=100)
    with terminal("queue", "tui") as ui:
        ui.read_until("电影合辑")
        ui.resize(8, 50)
        ui.send("?")
        ui.read_until("快捷键帮助")
        ui.send("prc JKt+-i")
        ui.send("q\r")
        ui.read_until("完整详情")
        ui.resize(12, 60)
        ui.send("\x1b[F")
        ui.read_until("详情为打开时的快照")
        ui.send("\x1b")
        ui.send("q")
        assert ui.finish() == 0
        assert b"\x1b[?1049l" in ui.data
    with QueueStore() as store:
        row = store.get(1)
        assert row.state == "pending" and row.completed == 50
        assert row.priority == 1 and not row.pinned


def test_picker_help_search_focus_and_cancel_do_not_download(seed, tmp_path):
    output = tmp_path / "downloads"
    with terminal(str(seed.torrent_path), "-o", str(output)) as ui:
        ui.read_until("选择下载文件")
        ui.resize(8, 50)
        ui.send("\tsecond\x1bOP")
        ui.read_until("快捷键帮助")
        ui.send("ai ")
        ui.send("q\t")
        ui.send("q")
        assert ui.finish() == 0
        assert not output.exists()
        assert b"\x1b[?1049l" in ui.data
