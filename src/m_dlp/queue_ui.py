"""Live queue panel; UI and owned runner each use their own SQLite connection."""

import asyncio
import sqlite3
from threading import Event, Thread

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl, UIContent, UIControl
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import TextArea
from rich.filesize import decimal

from .persistent_queue import run_persistent
from .picker import display_text
from .queue_cli import LABELS, PRIORITY_LABELS
from .queue_store import STATES, QueueStore
from .queue_view import QueueView

STATE_FILTERS = (None, *STATES)


def state_label(state):
    return LABELS[state] if state is not None else "全部"


def percentage(row):
    if row.state == "completed":
        return "100.0%"
    return f"{min(100, row.completed / row.total * 100):.1f}%" if row.total else "--"


def cell(text, width):
    """Pad/truncate by terminal columns, including CJK names."""
    value, used = "", 0
    for char in text:
        size = get_cwidth(char)
        if used + size > width:
            break
        value += char
        used += size
    return value + " " * (width - used)


class QueueRunner:
    def __init__(self, path, jobs, worker):
        self.path, self.jobs, self.worker = path, jobs, worker
        self.stopped = Event()
        self.error = ""
        self.notice = ""
        self.thread = Thread(target=self._run, name="m-dlp-queue")

    def _event(self, event):
        if event.kind == "result" and event.data.warnings:
            self.notice = f"任务 {event.task.number} 提示：" + "；".join(event.data.warnings)

    def _run(self):
        try:
            with QueueStore(self.path) as store:
                run_persistent(
                    store,
                    self.jobs,
                    self.worker,
                    self._event,
                    cancelled=self.stopped,
                    keep_alive=True,
                )
        except Exception as exc:
            self.error = str(exc)


class TaskControl(UIControl):
    def __init__(self, panel):
        self.panel = panel

    def is_focusable(self):
        return True

    def create_content(self, width, height):
        panel = self.panel
        if panel.choosing_state:

            def option(index):
                state = STATE_FILTERS[index]
                focused = index == panel.filter_index
                text = ("> " if focused else "  ") + state_label(state)
                if state is not None:
                    text += f" ({state})"
                return [("class:cursor" if focused else "", text)]

            return UIContent(
                get_line=option,
                line_count=len(STATE_FILTERS),
                cursor_position=Point(x=0, y=panel.filter_index),
                show_cursor=False,
            )

        def line(index):
            if not panel.rows:
                if panel.model.records:
                    return [("class:muted", "没有匹配任务；Esc 清空搜索和状态筛选。")]
                return [("class:muted", "没有任务；可在另一个终端使用 queue add 添加。")]
            row = panel.rows[index]
            focused = index == panel.cursor
            prefix = (
                ("> " if focused else "  ")
                + ("[x] " if row.id in panel.model.selected else "[ ] ")
                + cell(str(row.id), 6)
                + cell(("顶 " if row.pinned else "   ") + PRIORITY_LABELS[row.priority], 8)
                + cell(LABELS[row.state], 10)
                + cell(percentage(row), 8)
            )
            if width >= 100:
                prefix += (
                    cell(f"{decimal(row.completed)}/{decimal(row.total)}", 22)
                    + cell(f"{decimal(row.rate)}/s", 14)
                    + cell(str(row.peers), 6)
                )
            text = prefix + display_text(row.name or row.key[:16])
            return [("class:cursor" if focused else "", text)]

        return UIContent(
            get_line=line,
            line_count=max(1, len(panel.rows)),
            cursor_position=Point(x=0, y=panel.cursor),
            show_cursor=False,
        )


class QueuePanel:
    def __init__(
        self, store, *, jobs=None, worker=None, input=None, output=None, refresh_interval=0.5
    ):
        self.store = store
        self.model = QueueView()
        self.choosing_state = False
        self.filter_index = 0
        self.message = ""
        self.refresh_error = ""
        self.closing = False
        self.refresh_interval = refresh_interval
        self.runner = QueueRunner(store.path, jobs, worker) if jobs is not None else None
        self.table = TaskControl(self)
        self.window = Window(
            self.table, wrap_lines=False, right_margins=[ScrollbarMargin(display_arrows=True)]
        )
        self.search = TextArea(multiline=False, height=1, prompt="搜索 / ", wrap_lines=False)
        self.search.buffer.on_text_changed += self._search_changed
        root = HSplit(
            [
                Window(FormattedTextControl(self.title), height=1, style="class:title"),
                self.search,
                Window(FormattedTextControl(self.scope), height=1, style="class:title"),
                Window(FormattedTextControl(self.header), height=1, style="class:muted"),
                self.window,
                ConditionalContainer(
                    Window(FormattedTextControl(self.detail), height=4, wrap_lines=False),
                    filter=Condition(
                        lambda: self.app.output.get_size().rows >= 17 and not self.choosing_state
                    ),
                ),
                Window(FormattedTextControl(self.footer), height=3, wrap_lines=False),
                Window(
                    FormattedTextControl(self.help),
                    height=3,
                    style="class:muted",
                ),
            ]
        )
        self.app = Application(
            layout=Layout(root, focused_element=self.table),
            key_bindings=self._bindings(),
            full_screen=True,
            mouse_support=False,
            input=input,
            output=output,
            style=Style.from_dict(
                {"title": "bold", "cursor": "reverse", "muted": "ansibrightblack"}
            ),
        )
        self.app.ttimeoutlen = 0.05

    @property
    def current(self):
        return self.model.current

    @property
    def rows(self):
        return self.model.rows

    @property
    def cursor(self):
        return self.model.cursor

    def _search_changed(self, buffer):
        self.model.search(buffer.text)
        self.message = ""

    def refresh(self):
        try:
            rows = self.store.snapshot()
        except sqlite3.Error as exc:
            self.refresh_error = f"刷新失败（将自动重试）：{exc}"
            return
        self.refresh_error = ""
        self.model.update(rows)

    def move(self, amount):
        if self.choosing_state:
            self.filter_index = max(0, min(len(STATE_FILTERS) - 1, self.filter_index + amount))
        else:
            self.model.move(amount)
        self.message = ""

    def control(self, action):
        if self.closing:
            return
        ids = self.model.targets()
        if not ids:
            self.message = "没有匹配任务可操作。" if self.model.records else "没有任务可操作。"
            return
        try:
            records = self.store.control(ids, action)
            if self.model.selected:
                verb = {"pause": "暂停", "resume": "恢复", "cancel": "取消"}[action]
                self.message = f"已提交{verb}：{len(records)} 个任务。"
            else:
                self.message = f"任务 {records[0].id}：{LABELS[records[0].state]}。"
        except ValueError as exc:
            prefix = "批量操作未执行" if self.model.selected else "操作失败"
            self.message = f"{prefix}：{exc}"
        except sqlite3.Error as exc:
            self.message = f"操作失败：{exc}"
        self.refresh()

    def reorder(self, action):
        if self.closing:
            return
        ids = self.model.targets()
        if not ids:
            self.message = "没有匹配任务可操作。" if self.model.records else "没有任务可操作。"
            return
        try:
            if action in {"up", "down"}:
                before = [row.id for row in self.store.snapshot()]
                self.store.move(ids, action)
                after = [row.id for row in self.store.snapshot()]
                verb = "上移" if action == "up" else "下移"
                self.message = (
                    f"已{verb} {len(ids)} 个任务（按完整队列，同置顶状态、同优先级组内）。"
                    if before != after
                    else "顺序未变：已到同置顶状态、同优先级组的边界。"
                )
            elif action == "pin":
                records = self.store.pin(ids)
                verb = "置顶" if records[0].pinned else "取消置顶"
                self.message = f"已{verb} {len(records)} 个任务。"
            else:
                records = self.store.adjust_priority(ids, 1 if action == "raise" else -1)
                verb = "提高" if action == "raise" else "降低"
                self.message = f"已{verb} {len(records)} 个任务的优先级（高 / 普通 / 低）。"
        except (ValueError, sqlite3.Error) as exc:
            self.message = f"排序操作失败：{exc}"
        self.refresh()

    def title(self):
        records = self.model.records
        running = sum(row.state == "running" for row in records)
        paused = sum(row.state == "paused" for row in records)
        rate = sum(row.rate for row in records if row.state == "running")
        return f"任务队列 · {len(records)} 个 · 运行 {running} · 暂停 {paused} · {decimal(rate)}/s"

    def scope(self):
        return (
            f"已选 {len(self.model.selected)}（隐藏 {self.model.hidden_selected}） · "
            f"显示 {len(self.rows)}/{len(self.model.records)} · "
            f"状态：{state_label(self.model.state)}"
        )

    def help(self):
        if self.app.layout.has_focus(self.search):
            return (
                "输入名称 / ID / hash / 目录 · Enter 返回列表\nEsc 清空搜索和状态筛选 · Ctrl+C 退出"
            )
        if self.choosing_state:
            return "↑↓ 选择状态 · Enter 应用 · Esc 取消\nHome/End 跳转 · PgUp/PgDn 翻页"
        target = "全部已选（含隐藏）" if self.model.selected else "当前任务"
        return (
            "空格 多选 · / 搜索 · f 筛选 · a 全选结果 · n 清空 · q 退出\n"
            f"p 暂停 · r 恢复 · c 取消 → {target} · Esc 清筛选\n"
            "K/J 上下移（完整队列同级组） · t 置顶 · +/- 优先级"
        )

    def header(self):
        if self.choosing_state:
            return "选择状态：Enter 应用，Esc 保留原筛选"
        text = "      " + cell("ID", 6) + cell("优先级", 8) + cell("状态", 10) + cell("进度", 8)
        if self.app.output.get_size().columns >= 101:  # table reserves a scrollbar column
            text += cell("已下载/总大小", 22) + cell("速度", 14) + cell("节点", 6)
        return text + "资源"

    def detail(self):
        row = self.current
        if row is None:
            return "当前：无任务"
        return (
            f"任务 {row.id} · 优先级：{PRIORITY_LABELS[row.priority]}"
            f"{' · 置顶' if row.pinned else ''} · {display_text(row.name or row.key)}\n"
            f"{LABELS[row.state]} · {percentage(row)} · {decimal(row.completed)} / "
            f"{decimal(row.total)} · {decimal(row.rate)}/s · {row.peers} 个节点\n"
            f"目录：{display_text(row.destination)}\n"
            f"错误：{display_text(row.error) if row.error else '无'}"
        )

    def footer(self):
        if self.closing and self.runner:
            mode = "正在保存进度并停止本面板的下载，请稍候…"
        elif self.runner is None:
            mode = "管理模式 · 恢复仅入队，请确保 queue run 正在运行；退出不影响其他进程。"
        elif self.runner.error:
            mode = "下载进程已停止：" + self.runner.error
        else:
            mode = f"交互运行 · 并发 {self.runner.jobs} · 空闲时等待添加/恢复任务；q 保存后退出。"
        notice = self.message or (self.runner.notice if self.runner else "")
        return (
            display_text(mode)
            + "\n"
            + display_text(self.refresh_error or notice)
            + "\n队列："
            + display_text(str(self.store.path))
        )

    def _bindings(self):
        bindings = KeyBindings()
        available = Condition(lambda: not self.closing)
        table_focus = has_focus(self.table) & available
        list_focus = table_focus & Condition(lambda: not self.choosing_state)
        choosing = table_focus & Condition(lambda: self.choosing_state)
        search_focus = has_focus(self.search) & available

        def bind(key, action, *, filter=list_focus):
            @bindings.add(key, filter=filter)
            def perform(event):
                self.message = ""
                action()

        def count():
            return len(STATE_FILTERS) if self.choosing_state else len(self.rows)

        bind("up", lambda: self.move(-1), filter=table_focus)
        bind("down", lambda: self.move(1), filter=table_focus)
        bind("home", lambda: self.move(-count()), filter=table_focus)
        bind("end", lambda: self.move(count()), filter=table_focus)
        bind("pageup", lambda: self.move(-self._page_size()), filter=table_focus)
        bind("pagedown", lambda: self.move(self._page_size()), filter=table_focus)
        bind(" ", self.model.toggle)
        bind("a", self.model.select_visible)
        bind("n", self.model.clear_selection)
        bind("p", lambda: self.control("pause"))
        bind("r", lambda: self.control("resume"))
        bind("c", lambda: self.control("cancel"))
        bind("K", lambda: self.reorder("up"))
        bind("J", lambda: self.reorder("down"))
        bind("t", lambda: self.reorder("pin"))
        bind("+", lambda: self.reorder("raise"))
        bind("-", lambda: self.reorder("lower"))

        @bindings.add("/", filter=list_focus)
        def search(event):
            self.message = ""
            event.app.layout.focus(self.search)

        @bindings.add("enter", filter=search_focus)
        @bindings.add("down", filter=search_focus)
        def finish_search(event):
            event.app.layout.focus(self.table)

        @bindings.add("f", filter=list_focus)
        def choose_state(event):
            self.message = ""
            self.filter_index = STATE_FILTERS.index(self.model.state)
            self.choosing_state = True

        @bindings.add("enter", filter=choosing)
        def apply_state(event):
            self.model.filter_state(STATE_FILTERS[self.filter_index])
            self.choosing_state = False

        @bindings.add("escape", filter=available)
        def escape(event):
            self.message = ""
            if self.choosing_state:
                self.choosing_state = False
            else:
                self.search.text = ""
                self.model.filter_state(None)
            event.app.layout.focus(self.table)

        for key, code in (("q", 0), ("c-c", 130), ("c-d", 130), ("<sigint>", 130)):
            # Keep exit bindings active while closing so repeated interrupts do
            # not bypass worker cleanup through prompt_toolkit's default handler.
            @bindings.add(key, filter=has_focus(self.table) if key == "q" else True)
            def leave(event, code=code):
                if not self.closing:
                    self.closing = True
                    self.app.create_background_task(self._exit(code))

        return bindings

    def _page_size(self):
        info = self.window.render_info
        return max(1, info.window_height - 1) if info else 10

    async def _exit(self, code):
        if self.runner:
            self.runner.stopped.set()
            while self.runner.thread.is_alive():
                await asyncio.sleep(0.05)
            if code == 0 and self.runner.error:
                code = 1
        self.app.exit(result=code)

    async def _refresh_loop(self):
        while True:
            self.refresh()
            self.app.invalidate()
            await asyncio.sleep(self.refresh_interval)

    async def run_async(self):
        self.refresh()
        try:
            if self.runner:
                self.runner.thread.start()
            return await self.app.run_async(
                pre_run=lambda: self.app.create_background_task(self._refresh_loop())
            )
        except EOFError:
            return 130
        finally:
            if self.runner and self.runner.thread.ident is not None:
                self.runner.stopped.set()
                await asyncio.to_thread(self.runner.thread.join)

    def run(self):
        return asyncio.run(self.run_async())


def run_panel(path, *, jobs=None):
    from .queue_cli import _worker

    # Bound contention in the UI; the download thread keeps the regular timeout.
    with QueueStore(path, timeout=0.05) as store:
        return QueuePanel(store, jobs=jobs, worker=_worker).run()
