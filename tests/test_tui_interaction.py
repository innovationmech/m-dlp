import asyncio

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth
from test_queue_store import spec
from test_queue_ui import until
from test_queue_ui_bulk import with_panel

from m_dlp.engine import TorrentFile
from m_dlp.picker_ui import FilePicker
from m_dlp.queue_store import QueueStore
from m_dlp.queue_ui import QueuePanel


class SizedOutput(DummyOutput):
    def __init__(self, columns, rows=24):
        self.size = Size(rows=rows, columns=columns)

    def get_size(self):
        return self.size


@pytest.mark.parametrize("width", [32, 40, 59, 60, 80, 100, 140])
def test_queue_keeps_name_and_progress_visible_without_overflow(tmp_path, width):
    with QueueStore(tmp_path / "queue.db") as store, create_pipe_input() as pipe:
        store.add([(spec(tmp_path), {}, None)])
        store.progress(1, name="电影合辑-" + "很长的名字" * 30, completed=50, total=100)
        store.pin([1])
        panel = QueuePanel(store, input=pipe, output=SizedOutput(width + 1))
        panel.refresh()
        fragments = panel.table.create_content(width, 10).get_line(0)
        line = "".join(t for _, t in fragments)
        assert get_cwidth(line) <= width
        assert "电影" in line and "50.0%" in line
        assert "[ ]" in line
        assert "…" in line
        if width >= 59:
            assert "顶 普通" in line
        assert get_cwidth(panel.header()) <= width


@pytest.mark.parametrize("width", [24, 40, 80, 120])
def test_deep_file_names_remain_visible_and_size_is_aligned(width):
    files = [TorrentFile(7, "目录/" * 30 + "电影-" + "长名称" * 20 + ".mkv", 1024)]
    with create_pipe_input() as pipe:
        picker = FilePicker(files, "资源", input=pipe, output=SizedOutput(width + 1))
        picker.model.move(100)
        content = picker.tree.create_content(width, 10)
        line = "".join(t for _, t in content.get_line(picker.model.cursor))
        assert get_cwidth(line) <= width
        assert "电影" in line and "…" in line
        if width >= 40:
            assert "1.0 kB" in line and "#7" in line


def test_narrow_picker_summary_keeps_hidden_selections_and_exact_size_visible():
    files = [TorrentFile(1, "电影.mkv", 2500085000), TorrentFile(2, "字幕.srt", 20)]
    with create_pipe_input() as pipe:
        picker = FilePicker(files, "资源", input=pipe, output=SizedOutput(50, 12))
        picker.model.selected.add(1)
        picker.search.text = ".srt"
        summary = picker.footer().splitlines()[0]
        assert get_cwidth(summary) <= 50
        assert "搜索隐藏 1" in summary
        assert "2500085000 字节" in summary


def test_queue_help_and_details_do_not_dispatch_task_commands(tmp_path):
    async def scenario(panel, pipe, writer):
        long_error = "失败原因：" + "路径很长/" * 60 + "末尾错误"
        writer.progress(1, name="任务一")
        writer.claim({1})
        writer.finish(1, "failed", error=long_error)
        await until(lambda: panel.current.state == "failed")
        pipe.send_text(" /Movie\r?")  # hide selected task 1, then open help
        await until(lambda: panel.popup.visible)
        pipe.send_text("prc anJKt+-i")
        await asyncio.sleep(0.05)
        assert writer.get(1).state == "failed" and panel.model.selected == {1}
        pipe.send_text("q")  # closes help, does not quit the panel
        await until(lambda: not panel.popup.visible)
        assert panel.model.query == "Movie"
        pipe.send_text("\x1b\r")  # clear search; inspect the focused task
        await until(lambda: panel.popup.visible)
        assert panel.current.id == 2
        assert str(spec(tmp_path, 2).destination) in panel.popup.body.text
        pipe.send_text("\r\x1b[H\r")
        await until(lambda: "末尾错误" in panel.popup.body.text)
        pipe.send_text("\x1b[F")
        await until(lambda: panel.popup.body.buffer.cursor_position > 0)
        pipe.send_text("\x1b")
        await until(lambda: not panel.popup.visible)
        assert panel.current.id == 1 and panel.model.selected == {1}

    with_panel(tmp_path, scenario)


def test_queue_tab_search_and_invert_preserve_hidden_selection(tmp_path):
    async def scenario(panel, pipe, writer):
        pipe.send_text("\x1b[F \tMovie\t")
        await until(lambda: len(panel.rows) == 2 and panel.app.layout.has_focus(panel.table))
        assert panel.model.selected == {3}
        pipe.send_text("i")
        await until(lambda: panel.model.selected == {1, 2, 3})
        pipe.send_text("i")
        await until(lambda: panel.model.selected == {3})
        pipe.send_text("\x06?i")  # Ctrl+F focuses search; keys are ordinary text
        await until(lambda: panel.model.query == "Movie?i")
        assert not panel.popup.visible and panel.model.selected == {3}
        pipe.send_text("\x1b")
        await until(lambda: panel.app.layout.has_focus(panel.table))

    with_panel(tmp_path, scenario)


def test_picker_help_restores_search_focus_and_invert_only_changes_matches():
    async def run():
        files = [TorrentFile(1, "影片.mkv", 100), TorrentFile(2, "字幕.srt", 20)]
        with create_pipe_input() as pipe:
            picker = FilePicker(files, "资源", input=pipe, output=DummyOutput())
            task = asyncio.create_task(picker.app.run_async())
            try:
                pipe.send_text("a\t.mkv")
                await until(lambda: picker.model.query == ".mkv")
                pipe.send_text("\x1bOP")  # F1 opens help from search
                await until(lambda: picker.popup.visible)
                pipe.send_text("n i\r")  # no selection mutation; Enter closes help
                await until(lambda: not picker.popup.visible)
                assert picker.app.layout.has_focus(picker.search)
                assert picker.model.selected == {1, 2}
                pipe.send_text("\t i")  # deselect then invert the matching file
                await until(lambda: picker.model.selected == {1, 2})
                pipe.send_text("i\r")
                assert await asyncio.wait_for(task, 5) == {2}
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["queue", "picker"])
def test_short_terminal_retains_list_and_help_returns_focus(tmp_path, kind):
    async def run():
        output = SizedOutput(50, 8)
        with QueueStore(tmp_path / "queue.db") as store, create_pipe_input() as pipe:
            store.add([(spec(tmp_path), {}, None)])
            if kind == "queue":
                ui = QueuePanel(store, input=pipe, output=output)
                control = ui.table
                task = asyncio.create_task(ui.run_async())
            else:
                ui = FilePicker(
                    [TorrentFile(1, "文件.mkv", 100)], "资源", input=pipe, output=output
                )
                control = ui.tree
                task = asyncio.create_task(ui.app.run_async())
            try:
                await until(lambda: ui.window.render_info is not None)
                assert ui.window.render_info.window_height >= 2
                pipe.send_text("?")
                await until(lambda: ui.popup.visible)
                pipe.send_text("q")
                await until(lambda: ui.app.layout.has_focus(control))
                pipe.send_text("q")
                await asyncio.wait_for(task, 5)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
