"""Check real terminal output, including color degradation and unused space."""

import asyncio
import re
from io import StringIO

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.color_depth import ColorDepth
from prompt_toolkit.output.vt100 import Vt100_Output
from test_queue_store import spec
from test_queue_ui import until

from m_dlp.engine import TorrentFile
from m_dlp.picker_ui import FilePicker
from m_dlp.queue_store import QueueStore
from m_dlp.queue_ui import QueuePanel


def background_colors(text):
    """Read SGR parameters without mistaking RGB foreground values for commands."""
    colors = []
    for sgr in re.findall(r"\x1b\[([\d;]*)m", text):
        params = [int(value or 0) for value in sgr.split(";")]
        index = 0
        while index < len(params):
            code = params[index]
            if code in (38, 48):
                count = 3 if params[index + 1] == 5 else 5
                if code == 48:
                    colors.append(params[index : index + count])
                index += count
            else:
                # Reverse video also paints a foreground-colored rectangle.
                if code == 7 or 40 <= code <= 47 or 100 <= code <= 107:
                    colors.append(code)
                index += 1
    return colors


@pytest.mark.parametrize(
    "depth", [ColorDepth.DEPTH_4_BIT, ColorDepth.DEPTH_8_BIT, ColorDepth.DEPTH_24_BIT]
)
@pytest.mark.parametrize("view", ["empty", "queue", "picker", "help"])
def test_views_preserve_terminal_background_in_actual_ansi_output(
    tmp_path, monkeypatch, depth, view
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("PROMPT_TOOLKIT_COLOR_DEPTH", raising=False)

    async def run():
        stream = StringIO()
        output = Vt100_Output(
            stream,
            lambda: Size(rows=36, columns=120),
            default_color_depth=depth,
            enable_cpr=False,
        )
        with QueueStore(tmp_path / "queue.db") as store, create_pipe_input() as pipe:
            if view == "picker":
                ui = FilePicker(
                    [TorrentFile(i, f"目录/文件-{i}.mkv", i * 1024) for i in range(1, 60)],
                    "测试资源",
                    input=pipe,
                    output=output,
                )
                ui.model.selected.add(2)
                task = asyncio.create_task(ui.app.run_async())
            else:
                if view != "empty":
                    store.add([(spec(tmp_path, i), {}, None) for i in range(1, 60)])
                ui = QueuePanel(store, input=pipe, output=output)
                if view != "empty":
                    ui.model.selected.add(2)
                task = asyncio.create_task(ui.run_async())
            try:
                await until(lambda: ui.window.render_info is not None)
                if view == "help":
                    pipe.send_text("?")
                    await until(lambda: ui.popup.body.window.render_info is not None)
                raw = stream.getvalue()
                assert "\x1b[" in raw
                assert not background_colors(raw), (
                    "UI backgrounds must match untouched terminal cells"
                )
            finally:
                pipe.send_text("qq" if ui.popup.visible else "q")
                await asyncio.wait_for(task, 5)

    asyncio.run(run())


def test_scroll_indicator_only_appears_when_rows_overflow(tmp_path):
    async def run():
        with QueueStore(tmp_path / "queue.db") as store, create_pipe_input() as pipe:
            output = Vt100_Output(StringIO(), lambda: Size(rows=24, columns=100), enable_cpr=False)
            ui = QueuePanel(store, input=pipe, output=output, refresh_interval=0.01)
            task = asyncio.create_task(ui.run_async())
            try:
                await until(lambda: ui.window.render_info is not None)
                margin = ui.window.right_margins[-1]
                info = ui.window.render_info
                assert not margin.create_margin(info, 1, info.window_height)
                store.add([(spec(tmp_path, i), {}, None) for i in range(1, 80)])
                await until(lambda: ui.window.render_info.content_height == 79)
                info = ui.window.render_info
                assert margin.create_margin(info, 1, info.window_height)
            finally:
                pipe.send_text("q")
                await asyncio.wait_for(task, 5)

    asyncio.run(run())
