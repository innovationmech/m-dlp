import asyncio

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from test_picker_model import FILES

from m_dlp.engine import TorrentFile
from m_dlp.picker_ui import FilePicker


def choose(keys, files=FILES):
    async def run():
        with create_pipe_input() as pipe:
            picker = FilePicker(files, "资源", input=pipe, output=DummyOutput())
            pipe.send_text(keys)
            result = await asyncio.wait_for(picker.app.run_async(), timeout=3)
            return result, picker

    return asyncio.run(run())


def test_arrow_keys_space_and_confirm_return_original_numbers():
    result, _ = choose("\x1b[B \r")  # Extras/ -> bonus.mp4
    assert result == {8}


def test_live_search_and_directory_select_only_current_matches():
    result, picker = choose("/movie/.mkv\r\x1bq")
    assert result == set()  # no matches: Esc clears then q cancels
    result, picker = choose("/.MKV\r \r")
    assert result == {1, 5}
    assert picker.model.selected_size == 2304


def test_search_editing_space_is_text_and_unicode_paths_match():
    files = [TorrentFile(3, "目录/中文 字幕.srt", 12), TorrentFile(7, "目录/字幕.srt", 20)]
    result, _ = choose("/中文 字幕\r \r", files)
    assert result == {3}


def test_empty_enter_does_not_start_download_and_zero_byte_selection_is_valid():
    result, picker = choose("\r\x1b[F \r")
    assert result == {11}
    assert picker.model.selected_size == 0


def test_select_all_clear_and_navigation_after_collapse():
    result, _ = choose("an\x1b[C\x1b[D \r")
    assert result == {8}


def test_eof_aborts_picker():
    with pytest.raises(EOFError):
        choose("\x04")


def test_closed_input_aborts_picker():
    async def run():
        with create_pipe_input() as pipe:
            picker = FilePicker(FILES, "资源", input=pipe, output=DummyOutput())
            pipe.close()
            await asyncio.wait_for(picker.app.run_async(), timeout=3)

    with pytest.raises(EOFError):
        asyncio.run(run())


def test_footer_updates_and_reports_hidden_selections_while_searching():
    async def run():
        with create_pipe_input() as pipe:
            picker = FilePicker(FILES, "资源", input=pipe, output=DummyOutput())
            task = asyncio.create_task(picker.app.run_async())

            async def until(predicate):
                async with asyncio.timeout(3):
                    while not predicate():
                        await asyncio.sleep(0.01)

            try:
                pipe.send_text(" ")  # select Extras/
                await until(lambda: picker.model.selected == {8})
                assert "1024 字节" in picker.summary()
                pipe.send_text("/.mkv")
                await until(lambda: picker.model.query == ".mkv")
                assert "搜索隐藏 1" in picker.summary()
                pipe.send_text("\r \r")
                assert await asyncio.wait_for(task, 3) == {1, 5, 8}
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_scroll_to_last_item_and_resize_keeps_selection():
    files = [TorrentFile(i + 1, f"dir/{i:04}.bin", i) for i in range(300)]
    result, picker = choose("\x1b[F \r", files)
    assert result == {300}
    content = picker.tree.create_content(20, 4)
    assert content.cursor_position.y == len(picker.model.rows) - 1
    assert len(content.get_line(300)) > 0


def test_torrent_text_cannot_insert_terminal_controls_or_extra_rows():
    files = [TorrentFile(1, "bad\x1b[31m\nfile.bin", 3)]
    result, picker = choose(" \r", files)
    assert result == {1}
    line = "".join(text for _, text in picker.tree.create_content(80, 24).get_line(0))
    assert "\\x1b" in line and "\\n" in line and "\x1b" not in line
