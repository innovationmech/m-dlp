"""Live queue panel; UI and owned runner each use their own SQLite connection."""

import asyncio
import sqlite3
from threading import Event, Thread

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout
from prompt_toolkit.layout.controls import FormattedTextControl, UIContent, UIControl
from prompt_toolkit.widgets import TextArea
from rich.filesize import decimal

from .persistent_queue import run_persistent
from .picker import display_text
from .queue_cli import LABELS, PRIORITY_LABELS
from .queue_store import STATES, QueueStore
from .queue_view import QueueView
from .tui import (
    STYLE,
    ReadingView,
    ScrollIndicator,
    Spacing,
    cell,
    column_cell,
    empty_content,
    pad_text_area,
    padded_window,
)

STATE_FILTERS = (None, *STATES)

QUEUE_HELP = """任务队列 · 快捷键

浏览
  ↑ / ↓ 或 k / j     移动焦点
  Home / End          首项 / 末项
  PageUp / PageDown    翻页
  Enter               查看当前任务完整详情（可滚动）
  ? / F1              查看本帮助

搜索与筛选
  / 或 Ctrl+F         搜索名称、ID、hash、输出目录
  Tab / Shift+Tab     在搜索框与列表间切换
  搜索时 Enter / ↓    返回列表，保留搜索
  f                   选择状态；Enter 应用，Esc 取消
  Esc                 清空搜索和状态筛选，保留勾选

多选
  Space               勾选 / 取消当前任务
  a                   勾选所有可见任务
  i                   反选可见任务，保留隐藏勾选
  n                   清空全部勾选，包括隐藏任务

任务操作（有勾选时作用于全部已选，否则作用于当前任务）
  p / r               暂停 / 恢复或重试
  c                   取消任务，保留文件；取消后不能直接恢复
  K / J（大写）       同置顶状态、同优先级组内上移 / 下移
  t                   置顶；全部已置顶时取消置顶
  + / -               提高 / 降低优先级

退出
  q                   退出面板
  Ctrl+C / Ctrl+D     中断退出（包括帮助和详情页）

批量操作包含被搜索或状态筛选隐藏的已选任务。
任务按“置顶 → 优先级 → 手动顺序”调度，移动按完整队列计算。
改变顺序不会打断正在下载的任务。

管理模式只修改队列；恢复任务后需有 queue run 进程才能下载。
交互运行模式退出时会保存进度，再停止本面板的下载进程。
"""


def state_label(state):
    return LABELS[state] if state is not None else "全部"


def percentage(row):
    if row.state == "completed":
        return "100.0%"
    return f"{min(100, row.completed / row.total * 100):.1f}%" if row.total else "--"


def task_columns(width):
    """Share column widths between the header and rows; reserve space for names."""
    columns = [("mark", 6), ("id", 6 if width >= 70 else 4), ("name", 0)]
    if width >= 55:  # Keep priority in 60-column terminals after margins and scrollbar.
        columns.append(("priority", 10))
    columns.extend([("state", 10 if width >= 50 else 6), ("progress", 18 if width >= 100 else 8)])
    if width >= 85:
        columns.append(("rate", 12))
    if width >= 120:
        columns.append(("size", 22))
    if width >= 145:
        columns.append(("peers", 6))
    columns[2] = ("name", max(0, width - sum(size for _, size in columns)))
    return columns


def progress_text(row, width):
    if width < 18:
        return percentage(row)
    if not row.total and row.state != "completed":
        return "────────  --"
    ratio = 1 if row.state == "completed" else min(1, max(0, row.completed / row.total))
    filled = int(ratio * 8)
    return "━" * filled + "─" * (8 - filled) + " " + percentage(row)


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
                count = (
                    sum(row.state == state for row in panel.model.records)
                    if state
                    else len(panel.model.records)
                )
                text += f" · {count} 个"
                return [("class:mdlp.cursor" if focused else "", cell(text, width))]

            return UIContent(
                get_line=option,
                line_count=len(STATE_FILTERS),
                cursor_position=Point(x=0, y=panel.filter_index),
                show_cursor=False,
            )

        if not panel.rows:
            if panel.model.records:
                return empty_content(
                    width,
                    height,
                    "没有匹配的任务",
                    "尝试其他关键词或状态筛选。",
                    "Esc 清空筛选 · / 重新搜索",
                )
            return empty_content(
                width,
                height,
                "队列为空",
                "在另一个终端添加任务后，这里会自动更新。",
                "m-dlp queue add <磁力链接或种子文件> --all",
            )

        def line(index):
            row = panel.rows[index]
            focused = index == panel.cursor
            values = {
                "mark": ("> " if focused else "  ")
                + ("[x] " if row.id in panel.model.selected else "[ ] "),
                "id": str(row.id),
                "name": row.name or row.key[:16],
                "priority": ("顶 " if row.pinned else "   ") + PRIORITY_LABELS[row.priority],
                "state": LABELS[row.state]
                if width >= 50
                else {
                    "pending": "等待",
                    "running": "运行",
                    "pausing": "暂停中",
                    "paused": "暂停",
                    "cancelling": "取消中",
                    "cancelled": "取消",
                    "completed": "完成",
                    "failed": "失败",
                }[row.state],
                "progress": progress_text(row, 18 if width >= 100 else 8),
                "rate": f"{decimal(row.rate)}/s",
                "size": f"{decimal(row.completed)}/{decimal(row.total)}",
                "peers": str(row.peers),
            }
            fragments, remaining = [], width
            for key, size in task_columns(width):
                size = min(size, remaining)
                style = (
                    "class:mdlp.cursor"
                    if focused
                    else (
                        "class:mdlp.selected"
                        if key == "mark" and row.id in panel.model.selected
                        else "class:mdlp.priority"
                        if key == "priority" and (row.pinned or row.priority == 2)
                        else f"class:mdlp.{row.state}"
                        if key in {"state", "progress"}
                        else ""
                        if key == "name"
                        else "class:mdlp.muted"
                    )
                )
                value = column_cell(values[key].rstrip(), size, gap=1 if key == "mark" else 2)
                fragments.append((style, value))
                remaining -= size
            return fragments

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
        self.spacing = Spacing(lambda: self.app.output.get_size())
        self.popup = ReadingView(self.spacing)
        self.refresh_error = ""
        self.closing = False
        self.refresh_interval = refresh_interval
        self.runner = QueueRunner(store.path, jobs, worker) if jobs is not None else None
        self.table = TaskControl(self)
        self.window = padded_window(self.table, wrap_lines=False, right_margins=[ScrollIndicator()])
        self.search = TextArea(
            multiline=False,
            height=1,
            prompt="搜索 / ",
            wrap_lines=False,
            style="class:mdlp.query",
        )
        pad_text_area(self.search)
        self.search.window.style = lambda: (
            "class:mdlp.query.focused"
            if self.app.layout.has_focus(self.search)
            else "class:mdlp.query"
        )
        self.search.buffer.on_text_changed += self._search_changed
        root = HSplit(
            [
                padded_window(
                    FormattedTextControl(self.banner), height=1, style="class:mdlp.title"
                ),
                ConditionalContainer(
                    padded_window(
                        FormattedTextControl(self.title), height=1, style="class:mdlp.subtitle"
                    ),
                    filter=Condition(lambda: self.app.output.get_size().rows >= 12),
                ),
                self.spacing.rule(),
                ConditionalContainer(
                    self.search,
                    filter=Condition(
                        lambda: (
                            self.app.output.get_size().rows >= 10
                            or bool(self.search.text)
                            or self.app.layout.has_focus(self.search)
                        )
                    ),
                ),
                ConditionalContainer(
                    padded_window(
                        FormattedTextControl(self.scope), height=1, style="class:mdlp.status"
                    ),
                    filter=Condition(lambda: self.app.output.get_size().rows >= 10),
                ),
                ConditionalContainer(
                    padded_window(
                        FormattedTextControl(self.header), height=1, style="class:mdlp.header"
                    ),
                    filter=Condition(
                        lambda: (
                            self.app.output.get_size().rows >= 10
                            and bool(self.rows or self.choosing_state)
                        )
                    ),
                ),
                self.window,
                ConditionalContainer(
                    HSplit(
                        [
                            self.spacing.rule(),
                            padded_window(
                                FormattedTextControl(self.detail), height=4, wrap_lines=False
                            ),
                        ]
                    ),
                    filter=Condition(
                        lambda: (
                            self.app.output.get_size().rows >= 20
                            and not self.choosing_state
                            and self.current is not None
                        )
                    ),
                ),
                self.spacing.rule(),
                padded_window(
                    FormattedTextControl(self.footer),
                    height=2,
                    wrap_lines=False,
                    style=lambda: (
                        "class:mdlp.notice"
                        if self.message
                        or self.refresh_error
                        or (self.runner and (self.runner.notice or self.runner.error))
                        else "class:mdlp.hint"
                    ),
                ),
                self.spacing.gap(),
                padded_window(
                    FormattedTextControl(lambda: self.help().lstrip()),
                    height=1,
                    style="class:mdlp.hint",
                ),
            ]
        )
        self.app = Application(
            layout=Layout(self.spacing.frame(self.popup.wrap(root)), focused_element=self.table),
            key_bindings=self._bindings(),
            full_screen=True,
            mouse_support=False,
            input=input,
            output=output,
            style=STYLE,
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
        failed = sum(row.state == "failed" for row in records)
        rate = sum(row.rate for row in records if row.state == "running")
        return (
            f"共 {len(records)} 个 · 运行 {running} · 暂停 {paused} · "
            f"失败 {failed} · ↓ {decimal(rate)}/s"
        )

    def banner(self):
        mode = "交互运行" if self.runner else "管理模式"
        if self.closing:
            mode = "正在保存进度…"
        return f"m-dlp  /  任务队列  ·  {mode}"

    def scope(self):
        return (
            f"已选 {len(self.model.selected)}（隐藏 {self.model.hidden_selected}） · "
            f"显示 {len(self.rows)}/{len(self.model.records)} · "
            f"状态：{state_label(self.model.state)}"
        )

    def help(self):
        if self.app.layout.has_focus(self.search):
            return " F1 帮助 · Enter / Tab 返回 · Esc 清筛选"
        if self.choosing_state:
            return " ↑↓ 选择状态 · Enter 应用 · Esc 取消 · ? 帮助"
        width = self.app.output.get_size().columns
        if width < 70:
            return " ? 帮助 · q 退出 · / 搜索 · 空格 多选"
        if width < 100:
            return " ? 帮助 · / 搜索 · f 筛选 · Enter 详情 · q 退出"
        return " ? 帮助 · 空格 多选 · / 搜索 · f 筛选 · p 暂停 · r 恢复 · Enter 详情 · q 退出"

    def header(self):
        if self.choosing_state:
            return "选择状态：Enter 应用，Esc 保留原筛选"
        width = self.spacing.table_width
        labels = {
            "mark": "",
            "id": "ID",
            "name": "资源",
            "priority": "优先级",
            "state": "状态",
            "progress": "进度",
            "rate": "速度",
            "size": "已下载/总大小",
            "peers": "节点",
        }
        return cell(
            "".join(
                column_cell(labels[key], size, gap=1 if key == "mark" else 2)
                for key, size in task_columns(width)
            ),
            width,
        )

    def show_details(self):
        row = self.current
        if row is None:
            self.message = "没有匹配任务可查看。"
            return
        text = (
            f"{display_text(row.name or row.key)}\n\n"
            f"任务 ID    {row.id}\n状态       {LABELS[row.state]}\n"
            f"优先级     {PRIORITY_LABELS[row.priority]}{' · 置顶' if row.pinned else ''}\n"
            f"下载进度   {percentage(row)} · {decimal(row.completed)} / {decimal(row.total)}\n"
            f"传输速度   {decimal(row.rate)}/s\n连接节点   {row.peers}\n\n"
            f"输出目录\n{display_text(row.destination)}\n\n"
            f"Info hash\n{display_text(row.key)}\n\n"
            f"错误信息\n{display_text(row.error) if row.error else '无'}\n\n"
            f"队列数据库\n{display_text(str(self.store.path))}\n\n"
            "详情为打开时的快照；返回列表可查看最新进度。"
        )
        self.popup.open(self.app, f"任务 #{row.id} · 完整详情", text)

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
            mode = "管理模式 · 恢复后需 queue run 下载 · q 退出不影响其他进程"
        elif self.runner.error:
            mode = "下载进程已停止：" + self.runner.error
        else:
            mode = f"交互运行 · 并发 {self.runner.jobs} · 空闲时等待任务 · q 保存后退出"
        notice = self.message or (self.runner.notice if self.runner else "")
        target = (
            f"操作范围：已选 {len(self.model.selected)} 个（隐藏 {self.model.hidden_selected}）"
            if self.model.selected
            else f"操作范围：当前任务 #{self.current.id}"
            if self.current
            else "自动刷新 · / 搜索 · f 状态筛选 · ? 快捷键帮助"
        )
        return display_text(self.refresh_error or notice or target) + "\n" + display_text(mode)

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
        bind("k", lambda: self.move(-1), filter=table_focus)
        bind("j", lambda: self.move(1), filter=table_focus)
        bind("home", lambda: self.move(-count()), filter=table_focus)
        bind("end", lambda: self.move(count()), filter=table_focus)
        bind("pageup", lambda: self.move(-self._page_size()), filter=table_focus)
        bind("pagedown", lambda: self.move(self._page_size()), filter=table_focus)
        bind(" ", self.model.toggle)
        bind("a", self.model.select_visible)
        bind("n", self.model.clear_selection)
        bind("i", self.model.invert_visible)
        bind("enter", self.show_details)
        bind("p", lambda: self.control("pause"))
        bind("r", lambda: self.control("resume"))
        bind("c", lambda: self.control("cancel"))
        bind("K", lambda: self.reorder("up"))
        bind("J", lambda: self.reorder("down"))
        bind("t", lambda: self.reorder("pin"))
        bind("+", lambda: self.reorder("raise"))
        bind("-", lambda: self.reorder("lower"))

        @bindings.add("/", filter=list_focus)
        @bindings.add("c-f", filter=list_focus | search_focus)
        @bindings.add("tab", filter=list_focus)
        @bindings.add("s-tab", filter=list_focus)
        def search(event):
            self.message = ""
            event.app.layout.focus(self.search)

        @bindings.add("enter", filter=search_focus)
        @bindings.add("down", filter=search_focus)
        @bindings.add("tab", filter=search_focus)
        @bindings.add("s-tab", filter=search_focus)
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

        @bindings.add("escape", filter=table_focus | search_focus)
        def escape(event):
            self.message = ""
            if self.choosing_state:
                self.choosing_state = False
            else:
                self.search.text = ""
                self.model.filter_state(None)
            event.app.layout.focus(self.table)

        @bindings.add("?", filter=table_focus)
        @bindings.add("f1", filter=table_focus | search_focus)
        def help_view(event):
            self.popup.open(event.app, "任务队列 · 快捷键帮助", QUEUE_HELP)

        self.popup.bind(bindings)

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
