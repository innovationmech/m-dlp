"""Terminal adapter for the file selection model."""

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout
from prompt_toolkit.layout.controls import FormattedTextControl, UIContent, UIControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import TextArea
from rich.filesize import decimal

from .picker import SelectionModel, display_text
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

PICKER_HELP = """文件选择 · 快捷键

移动与展开
  ↑ / ↓ 或 k / j     移动光标
  ← / → 或 h / l     收起 / 展开目录，返回父目录 / 进入子项
  Home / End          跳到首项 / 末项
  PageUp / PageDown    翻页

选择文件
  Space               切换当前文件或目录的选择
  a / n               全选 / 清空当前搜索结果
  i                   反选当前搜索结果
  Enter               确认已选文件
  q                   取消选择并退出

搜索与帮助
  / 或 Ctrl+F         搜索完整路径，不区分大小写
  Tab / Shift+Tab     在搜索框与列表间切换
  搜索时 Enter / ↓    返回列表，保留搜索条件
  Esc                 清空搜索，返回列表
  ? / F1              打开帮助；搜索时 ? 是普通文字
  Ctrl+C / Ctrl+D     中断并退出

[ ] 未选择   [-] 目录部分选中   [x] 已选择

目录选择包含所有层级的文件。搜索时，目录选择、全选、清空和反选
仅作用于匹配文件，也包括收起目录中的匹配文件。
隐藏的已选文件会保留，确认时仍包含在结果中。
"""


class TreeControl(UIControl):
    def __init__(self, model):
        self.model = model

    def is_focusable(self):
        return True

    def create_content(self, width, height):
        model = self.model
        if not model.rows:
            return empty_content(
                width,
                height,
                "没有匹配的文件",
                "搜索会保留已经选择的文件。",
                "Esc 清空搜索 · / 重新搜索 · q 取消",
            )

        def line(index):
            entry = model.rows[index]
            members = model.members(entry)
            size = sum(model.files[number].size for number in members)
            focused = index == model.cursor
            arrow = ">" if focused else " "
            fold = ("▾" if model.is_expanded(entry) else "▸") if entry.directory else " "
            name = display_text(entry.name) + ("/" if entry.directory else "")
            suffix = (
                (f"{len(members)} 个文件" if width >= 70 else f"{len(members)}项")
                if entry.directory
                else f"#{next(iter(members))}"
            )
            # Bound indentation so deeply nested names survive small terminals.
            indent = min(entry.depth * 2, max(0, (width - 38) // 3), 16)
            prefix = f"{arrow} {model.mark(entry)} " + " " * indent + fold + " "
            size_width, count_width = (11, 12) if width >= 70 else (9, 5) if width >= 40 else (0, 0)
            name_width = max(0, width - len(prefix) - size_width - count_width)
            base = "class:mdlp.cursor" if focused else ""
            name_style = base or ("class:mdlp.directory" if entry.directory else "")
            mark_style = base or (
                "class:mdlp.selected" if model.mark(entry) != "[ ]" else "class:mdlp.muted"
            )
            return [
                (mark_style, cell(prefix, min(width, len(prefix)))),
                (name_style, column_cell(name, name_width)),
                (base or "class:mdlp.muted", column_cell(decimal(size), size_width, right=True)),
                (base or "class:mdlp.muted", column_cell(suffix, count_width, right=True, gap=1)),
            ]

        return UIContent(
            get_line=line,
            line_count=max(1, len(model.rows)),
            cursor_position=Point(x=0, y=model.cursor),
            show_cursor=False,
        )


class FilePicker:
    def __init__(self, files, name, *, input=None, output=None):
        self.model = SelectionModel(files)
        self.name = display_text(name)
        self.message = ""
        self.spacing = Spacing(lambda: self.app.output.get_size())
        self.popup = ReadingView(self.spacing)
        self.tree = TreeControl(self.model)
        self.window = padded_window(self.tree, wrap_lines=False, right_margins=[ScrollIndicator()])
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
        bindings = self._bindings()
        root = HSplit(
            [
                padded_window(
                    FormattedTextControl("m-dlp  /  选择下载文件"),
                    height=1,
                    style="class:mdlp.title",
                ),
                ConditionalContainer(
                    padded_window(
                        FormattedTextControl(lambda: self.name),
                        height=1,
                        style="class:mdlp.subtitle",
                    ),
                    filter=Condition(lambda: self.app.output.get_size().rows >= 10),
                ),
                self.spacing.rule(),
                self.search,
                ConditionalContainer(
                    padded_window(
                        FormattedTextControl(self.header), height=1, style="class:mdlp.header"
                    ),
                    filter=Condition(lambda: self.app.output.get_size().rows >= 10),
                ),
                self.window,
                ConditionalContainer(
                    HSplit(
                        [
                            self.spacing.rule(),
                            padded_window(
                                FormattedTextControl(self.detail),
                                height=Dimension(min=1, max=2),
                                wrap_lines=True,
                                dont_extend_height=True,
                            ),
                        ]
                    ),
                    filter=Condition(lambda: self.app.output.get_size().rows >= 16),
                ),
                self.spacing.rule(),
                padded_window(
                    FormattedTextControl(self.footer),
                    height=lambda: 2 if self.app.output.get_size().rows >= 10 else 1,
                    wrap_lines=False,
                    style="class:mdlp.status",
                ),
                self.spacing.gap(),
                padded_window(
                    FormattedTextControl(lambda: self.help().lstrip()),
                    height=1,
                    wrap_lines=False,
                    style="class:mdlp.hint",
                ),
            ]
        )
        self.app = Application(
            layout=Layout(self.spacing.frame(self.popup.wrap(root)), focused_element=self.tree),
            key_bindings=bindings,
            full_screen=True,
            mouse_support=False,
            input=input,
            output=output,
            style=STYLE,
        )
        self.app.ttimeoutlen = 0.05

    def _search_changed(self, buffer):
        self.model.search(buffer.text)
        self.message = ""

    def summary(self):
        model = self.model
        return (
            f"已选 {len(model.selected)}/{len(model.files)} 个 · "
            f"合计 {decimal(model.selected_size)}（{model.selected_size} 字节） · "
            f"搜索隐藏 {model.hidden_selected}"
        )

    def header(self):
        width = self.spacing.table_width
        sizes = (11, 12) if width >= 70 else (9, 5) if width >= 40 else (0, 0)
        return (
            cell(f"  文件 / 目录 · 匹配 {len(self.model.matches)} 个", width - sum(sizes))
            + column_cell("大小", sizes[0], right=True)
            + column_cell("编号 / 数量" if width >= 70 else "编号", sizes[1], right=True, gap=1)
        )

    def help(self):
        if self.app.layout.has_focus(self.search):
            return " F1 帮助 · Enter / Tab 返回 · Esc 清搜索"
        width = self.app.output.get_size().columns
        if width < 70:
            return " ? 帮助 · q 取消 · 空格 选择 · Enter 确认"
        if width < 100:
            return " ? 帮助 · 空格 选择 · / 搜索 · Enter 确认 · q 取消"
        return " ? 帮助 · ↑↓ 移动 · ←→ 展开 · 空格 选择 · / 搜索 · a 全选 · Enter 确认 · q 取消"

    def footer(self):
        hint = self.message or (
            "搜索中：Enter 返回列表，Esc 清空搜索；空格用于输入搜索词。"
            if self.app.layout.has_focus(self.search)
            else "Enter 确认所选文件 · 搜索隐藏的已选文件也会包含在内。"
            if self.model.selected
            else "按空格选择文件或整个目录，a 全选；? 查看全部快捷键。"
        )
        if self.app.output.get_size().rows < 10 and self.message:
            return self.message
        summary = self.summary()
        if self.app.output.get_size().columns < 70:
            model = self.model
            summary = (
                f"已选 {len(model.selected)}/{len(model.files)} · "
                f"搜索隐藏 {model.hidden_selected} · {model.selected_size} 字节"
            )
            hint = f"合计 {decimal(model.selected_size)} · " + hint
        return summary + "\n" + hint

    def detail(self):
        entry = self.model.current
        return "当前：" + display_text(entry.path) if entry else "当前：无匹配文件 · Esc 清空搜索"

    def _bindings(self):
        bindings = KeyBindings()
        tree_focus = has_focus(self.tree)
        search_focus = has_focus(self.search)

        def bind(key, action):
            @bindings.add(key, filter=tree_focus)
            def perform(event):
                self.message = ""
                action()

        bind("up", lambda: self.model.move(-1))
        bind("down", lambda: self.model.move(1))
        bind("k", lambda: self.model.move(-1))
        bind("j", lambda: self.model.move(1))
        bind("home", lambda: self.model.move(-len(self.model.rows)))
        bind("end", lambda: self.model.move(len(self.model.rows)))
        bind("pageup", lambda: self.model.move(-self._page_size()))
        bind("pagedown", lambda: self.model.move(self._page_size()))
        bind("left", self.model.left)
        bind("right", self.model.right)
        bind("h", self.model.left)
        bind("l", self.model.right)
        bind(" ", self.model.toggle)
        bind("a", lambda: self.model.select_matches(True))
        bind("n", lambda: self.model.select_matches(False))
        bind("i", self.model.invert_matches)

        @bindings.add("/", filter=tree_focus)
        @bindings.add("c-f", filter=tree_focus | search_focus)
        @bindings.add("tab", filter=tree_focus)
        @bindings.add("s-tab", filter=tree_focus)
        def search(event):
            self.message = ""
            event.app.layout.focus(self.search)

        @bindings.add("enter", filter=search_focus)
        @bindings.add("down", filter=search_focus)
        @bindings.add("tab", filter=search_focus)
        @bindings.add("s-tab", filter=search_focus)
        def finish_search(event):
            event.app.layout.focus(self.tree)

        @bindings.add("escape", filter=tree_focus | search_focus)
        def clear_search(event):
            self.search.text = ""
            self.message = ""
            event.app.layout.focus(self.tree)

        @bindings.add("enter", filter=tree_focus)
        def accept(event):
            if self.model.selected:
                event.app.exit(result=set(self.model.selected))
            else:
                self.message = "尚未选择文件；按空格选择，或 q 取消。"

        @bindings.add("q", filter=tree_focus)
        def cancel(event):
            event.app.exit(result=set())

        @bindings.add("?", filter=tree_focus)
        @bindings.add("f1", filter=tree_focus | search_focus)
        def help_view(event):
            self.popup.open(event.app, "文件选择 · 快捷键帮助", PICKER_HELP)

        self.popup.bind(bindings)

        @bindings.add("c-c")
        def interrupt(event):
            event.app.exit(exception=KeyboardInterrupt())

        @bindings.add("c-d")
        def end_input(event):
            event.app.exit(exception=EOFError())

        return bindings

    def _page_size(self):
        info = self.window.render_info
        return max(1, info.window_height - 1) if info else 10

    def run(self) -> set[int]:
        return self.app.run()


def choose_files(files, name) -> set[int]:
    return FilePicker(files, name).run()
