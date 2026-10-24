import asyncio
import sqlite3
from threading import Event
from types import SimpleNamespace

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from test_queue_store import spec

from m_dlp.batch import TaskEvent, TaskResult
from m_dlp.queue_store import QueueStore
from m_dlp.queue_ui import QueuePanel


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


def test_navigation_live_refresh_and_commands_target_stable_id(tmp_path):
    async def run():
        path = tmp_path / "queue.db"
        with QueueStore(path) as store, QueueStore(path) as writer, create_pipe_input() as pipe:
            store.add([(spec(tmp_path, n), {}, None) for n in range(1, 41)])
            panel = QueuePanel(store, input=pipe, output=DummyOutput(), refresh_interval=0.02)
            task = asyncio.create_task(panel.run_async())
            try:
                pipe.send_text("\x1b[Bp")
                await until(lambda: writer.get(2).state == "paused")
                assert writer.get(1).state == "pending"
                writer.progress(2, name="视频", completed=50, total=100, rate=12, peers=3)
                writer.add([(spec(tmp_path, 41), {}, None)])
                await until(lambda: len(panel.rows) == 41 and panel.current.completed == 50)
                assert panel.current.id == 2
                assert "50.0%" in panel.detail() and "12 bytes/s" in panel.detail()
                pipe.send_text("r")
                await until(lambda: writer.get(2).state == "pending")
                pipe.send_text("c")
                await until(lambda: writer.get(2).state == "cancelled")
                pipe.send_text("r")
                await until(lambda: "不支持" in panel.message)
                pipe.send_text("\x1b[F")
                await until(lambda: panel.current.id == 41)
                content = panel.table.create_content(40, 5)
                assert content.cursor_position.y == 40
                pipe.send_text("\x1b[5~")
                await until(lambda: panel.current.id < 41)
                pipe.send_text("\x1b[H")
                await until(lambda: panel.current.id == 1)
                pipe.send_text("q")
                assert await asyncio.wait_for(task, 5) == 0
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_empty_queue_safe_text_and_database_contention_are_visible(tmp_path):
    with QueueStore(tmp_path / "queue.db", timeout=0.02) as store:
        with create_pipe_input() as pipe:
            panel = QueuePanel(store, input=pipe, output=DummyOutput())
            panel.refresh()
            panel.control("pause")
            assert "没有任务" in panel.message
            store.add([(spec(tmp_path), {}, None)])
            store.progress(1, name="bad\x1b[31m\n名字", completed=10, total=100)
            panel.refresh()
            line = "".join(t for _, t in panel.table.create_content(120, 24).get_line(0))
            assert "\x1b" not in line and "\n" not in line
            assert "\\x1b" in panel.detail() and "\\n" in panel.detail()
            other = sqlite3.connect(store.path)
            try:
                other.execute("BEGIN EXCLUSIVE")
                panel.refresh()
                assert "刷新失败" in panel.footer()
                assert panel.current.id == 1
                panel.control("pause")
                assert "操作失败" in panel.message
            finally:
                other.rollback()
                other.close()
            panel.refresh()
            assert "刷新失败" not in panel.footer()
            assert panel.current.state == "pending"


@pytest.mark.parametrize(
    "key,code",
    [("q", 0), ("\x03", 130), ("\x04", 130), (None, 130), ("?\x03", 130), ("\r\x04", 130)],
)
def test_owned_runner_exit_joins_workers_and_preserves_progress(tmp_path, key, code):
    async def run():
        stopped_cleanly = Event()

        def worker(record, stopped, emit):
            emit(
                TaskEvent(
                    record.task,
                    "progress",
                    SimpleNamespace(completed=12, total=100, rate=4, peers=1),
                )
            )
            assert stopped.wait(5)
            stopped_cleanly.set()
            return TaskResult(record.task, "cancelled")

        with QueueStore(tmp_path / "queue.db") as store, create_pipe_input() as pipe:
            store.add([(spec(tmp_path), {}, None)])
            panel = QueuePanel(
                store,
                jobs=1,
                worker=worker,
                input=pipe,
                output=DummyOutput(),
                refresh_interval=0.02,
            )
            task = asyncio.create_task(panel.run_async())
            try:
                await until(lambda: panel.current and panel.current.completed == 12)
                if key is None:
                    pipe.close()
                else:
                    pipe.send_text(key)
                assert await asyncio.wait_for(task, 5) == code
                assert stopped_cleanly.is_set()
                assert not panel.runner.thread.is_alive()
                assert store.get(1).state == "pending"
                assert store.get(1).completed == 12
                assert not store.get(1).active
                with store.runner():
                    pass
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_runner_lock_failure_is_shown_without_stopping_other_runner(tmp_path):
    async def run():
        with QueueStore(tmp_path / "queue.db") as store, create_pipe_input() as pipe:
            store.add([(spec(tmp_path), {}, None)])
            with store.runner():
                store.claim()
                panel = QueuePanel(
                    store,
                    jobs=1,
                    worker=lambda *args: pytest.fail("duplicate"),
                    input=pipe,
                    output=DummyOutput(),
                    refresh_interval=0.02,
                )
                task = asyncio.create_task(panel.run_async())
                try:
                    await until(lambda: "已有下载进程" in panel.footer())
                    pipe.send_text("q")
                    assert await asyncio.wait_for(task, 5) == 1
                    assert store.get(1).active
                    assert store.get(1).state == "running"
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
