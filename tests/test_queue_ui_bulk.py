import asyncio

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from test_queue_store import spec
from test_queue_ui import until

from m_dlp.queue_store import QueueStore
from m_dlp.queue_ui import QueuePanel


def with_panel(tmp_path, scenario, *, exit_code=0):
    async def run():
        path = tmp_path / "queue.db"
        with QueueStore(path) as store, QueueStore(path) as writer, create_pipe_input() as pipe:
            store.add([(spec(tmp_path, n), {}, None) for n in (1, 2, 3)])
            for number, name in enumerate(("Movie One", "Movie Two", "中文 字幕"), 1):
                writer.progress(number, name=name)
            panel = QueuePanel(store, input=pipe, output=DummyOutput(), refresh_interval=0.02)
            task = asyncio.create_task(panel.run_async())
            try:
                await until(lambda: len(panel.rows) == 3)
                await scenario(panel, pipe, writer)
                if exit_code == 0:
                    pipe.send_text("q")
                assert await asyncio.wait_for(task, 5) == exit_code
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_search_text_is_not_interpreted_as_commands_and_escape_restores_list(tmp_path):
    async def scenario(panel, pipe, writer):
        pipe.send_text("/p r c q a n f 中文")
        await until(lambda: panel.model.query == "p r c q a n f 中文")
        assert not panel.rows and not panel.model.selected
        assert [r.state for r in writer.list()] == ["pending"] * 3
        pipe.send_text("\r")
        await until(lambda: panel.app.layout.has_focus(panel.table))
        pipe.send_text("p")
        await until(lambda: "没有匹配任务" in panel.message)
        pipe.send_text("\x1b")
        await until(lambda: len(panel.rows) == 3 and panel.model.query == "")
        pipe.send_text("/中文 字幕\r")
        await until(lambda: len(panel.rows) == 1 and panel.current.id == 3)
        assert panel.app.layout.has_focus(panel.table)

    with_panel(tmp_path, scenario)


@pytest.mark.parametrize("mode", ["search", "filter"])
@pytest.mark.parametrize("key", ["\x03", "\x04"])
def test_interrupt_works_with_search_or_filter_focus(tmp_path, mode, key):
    async def scenario(panel, pipe, writer):
        if mode == "search":
            pipe.send_text("/hello")
            await until(lambda: panel.model.query == "hello")
        else:
            pipe.send_text("f")
            await until(lambda: panel.choosing_state)
        pipe.send_text(key)

    with_panel(tmp_path, scenario, exit_code=130)


def test_space_multiselect_bulk_controls_include_hidden_selection(tmp_path):
    async def scenario(panel, pipe, writer):
        pipe.send_text(" \x1b[B ")
        await until(lambda: panel.model.selected == {1, 2})
        pipe.send_text("/字幕\r")
        await until(lambda: [r.id for r in panel.rows] == [3])
        assert "已选 2" in panel.scope() and "隐藏 2" in panel.scope()
        for key, state in (("p", "paused"), ("r", "pending"), ("c", "cancelled")):
            pipe.send_text(key)
            await until(lambda state=state: [writer.get(i).state for i in (1, 2)] == [state, state])
            assert writer.get(3).state == "pending"
            assert panel.model.selected == {1, 2}
        pipe.send_text("r")
        await until(lambda: "批量操作未执行" in panel.message and "不支持" in panel.message)
        pipe.send_text("np")
        await until(lambda: writer.get(3).state == "paused")
        assert not panel.model.selected

    with_panel(tmp_path, scenario)


def test_state_chooser_combines_with_search_and_preserves_hidden_selection(tmp_path):
    async def scenario(panel, pipe, writer):
        writer.control([2, 3], "pause")
        await until(lambda: panel.rows[1].state == "paused")
        pipe.send_text(" /movie\r")  # select task 1, then search
        await until(lambda: len(panel.rows) == 2)
        pipe.send_text("f" + "\x1b[B" * 4 + "\r")
        await until(lambda: panel.model.state == "paused")
        assert [r.id for r in panel.rows] == [2]
        assert panel.model.selected == {1} and panel.model.hidden_selected == 1
        pipe.send_text("a")
        await until(lambda: panel.model.selected == {1, 2})
        writer.control([2], "resume")
        await until(lambda: not panel.rows)
        assert panel.model.selected == {1, 2}
        pipe.send_text("f")
        await until(lambda: panel.choosing_state)
        pipe.send_text("\x1b[H")
        await until(lambda: panel.filter_index == 0)
        pipe.send_text("\x1b")  # cancel chooser, retaining paused filter
        await until(lambda: not panel.choosing_state)
        assert panel.model.state == "paused"
        pipe.send_text("\x1b")  # clear both filters, preserving selections
        await until(lambda: panel.model.state is None and len(panel.rows) == 3)
        assert panel.model.query == "" and panel.model.selected == {1, 2}

    with_panel(tmp_path, scenario)


def test_status_menu_ignores_task_commands_and_can_select_last_state(tmp_path):
    async def scenario(panel, pipe, writer):
        pipe.send_text(" f")
        await until(lambda: panel.choosing_state)
        pipe.send_text("prc an\x1b[F")
        await until(lambda: panel.filter_index == 8)
        assert panel.model.selected == {1}
        assert [r.state for r in writer.list()] == ["pending"] * 3
        pipe.send_text("\r")
        await until(lambda: panel.model.state == "failed" and not panel.choosing_state)
        assert not panel.rows
        assert "3 个" in panel.title() and "显示 0/3" in panel.scope()

    with_panel(tmp_path, scenario)


def test_batch_uses_transactional_current_states_and_rolls_back_on_race(tmp_path):
    async def scenario(panel, pipe, writer):
        pipe.send_text(" \x1b[B ")
        await until(lambda: panel.model.selected == {1, 2})
        # Change task 2 after the UI snapshot. Task 1 must not be paused alone.
        writer.claim({2})
        writer.finish(2, "completed")
        assert writer.get(2).state == "completed"
        pipe.send_text("p")
        await until(lambda: "批量操作未执行" in panel.message)
        assert writer.get(1).state == "pending"
        assert writer.get(2).state == "completed"
        assert panel.model.selected == {1, 2}
        pipe.send_text(" ")  # deselect task 2; failed action kept selection and cursor
        await until(lambda: panel.model.selected == {1})
        pipe.send_text("p")
        await until(lambda: writer.get(1).state == "paused")

    with_panel(tmp_path, scenario)
