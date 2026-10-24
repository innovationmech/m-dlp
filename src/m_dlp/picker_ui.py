"""Terminal adapter for the file selection model."""

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl, UIContent, UIControl
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea
from rich.filesize import decimal

from .picker import SelectionModel, display_text


class TreeControl(UIControl):
    def __init__(self, model):
        self.model = model

    def is_focusable(self):
        return True

    def create_content(self, width, height):
        model = self.model

        def line(index):
            if not model.rows:
                return [("class:muted", "没有匹配的文件；Esc 清空搜索，q 取消。")]
            entry = model.rows[index]
            members = model.members(entry)
            size = sum(model.files[number].size for number in members)
            arrow = ">" if index == model.cursor else " "
            fold = ("v" if model.is_expanded(entry) else ">") if entry.directory else " "
            name = display_text(entry.name) + ("/" if entry.directory else "")
            suffix = f"{len(members)} 个文件" if entry.directory else f"#{next(iter(members))}"
            text = (
                f"{arrow} {'  ' * entry.depth}{fold} {model.mark(entry)} "
                f"{name}  {decimal(size)}  {suffix}"
            )
            style = "class:cursor" if index == model.cursor else ""
            if entry.directory:
                style += " class:directory"
            return [(style, text)]

        return UIContent(
            get_line=line,
            line_count=max(1, len(model.rows)),
            cursor_position=Point(x=0, y=model.cursor),
            show_cursor=False,
        )


class FilePicker:
    def __init__(self, files, name, *, input=None, output=None):
        self.model = SelectionModel(files)
        self.message = ""
        self.tree = TreeControl(self.model)
        self.window = Window(
            self.tree, wrap_lines=False, right_margins=[ScrollbarMargin(display_arrows=True)]
        )
        self.search = TextArea(multiline=False, height=1, prompt="搜索 / ", wrap_lines=False)
        self.search.buffer.on_text_changed += self._search_changed
        bindings = self._bindings()
        root = HSplit(
            [
                Window(
                    FormattedTextControl("选择下载文件 · " + display_text(name)),
                    height=1,
                    style="class:title",
                ),
                self.search,
                self.window,
                ConditionalContainer(
                    Window(FormattedTextControl(self.detail), height=2, wrap_lines=True),
                    filter=Condition(lambda: self.app.output.get_size().rows >= 12),
                ),
                Window(
                    FormattedTextControl(self.footer),
                    height=2,
                    wrap_lines=True,
                    style="class:status",
                ),
                ConditionalContainer(
                    Window(
                        FormattedTextControl(
                            "↑↓ 移动 · ←→ 收起/展开 · 空格 多选/选目录 · / 搜索\n"
                            "a 全选结果 · n 清空结果 · Enter 确认 · Esc 清搜索 · q 取消"
                        ),
                        height=2,
                        wrap_lines=True,
                        style="class:muted",
                    ),
                    filter=Condition(lambda: self.app.output.get_size().rows >= 10),
                ),
            ]
        )
        self.app = Application(
            layout=Layout(root, focused_element=self.tree),
            key_bindings=bindings,
            full_screen=True,
            mouse_support=False,
            input=input,
            output=output,
            style=Style.from_dict(
                {
                    "title": "bold",
                    "cursor": "reverse",
                    "directory": "bold",
                    "status": "bold",
                    "muted": "ansibrightblack",
                }
            ),
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

    def footer(self):
        hint = self.message or (
            "搜索中：Enter 返回列表，Esc 清空搜索；空格用于输入搜索词。"
            if self.app.layout.has_focus(self.search)
            else "目录空格递归选择；搜索时仅影响匹配文件，已选内容始终保留。"
        )
        return self.summary() + "\n" + hint

    def detail(self):
        entry = self.model.current
        return "当前：" + display_text(entry.path) if entry else "当前：无匹配文件"

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
        bind("home", lambda: self.model.move(-len(self.model.rows)))
        bind("end", lambda: self.model.move(len(self.model.rows)))
        bind("pageup", lambda: self.model.move(-self._page_size()))
        bind("pagedown", lambda: self.model.move(self._page_size()))
        bind("left", self.model.left)
        bind("right", self.model.right)
        bind(" ", self.model.toggle)
        bind("a", lambda: self.model.select_matches(True))
        bind("n", lambda: self.model.select_matches(False))

        @bindings.add("/", filter=tree_focus)
        def search(event):
            self.message = ""
            event.app.layout.focus(self.search)

        @bindings.add("enter", filter=search_focus)
        @bindings.add("down", filter=search_focus)
        def finish_search(event):
            event.app.layout.focus(self.tree)

        @bindings.add("escape")
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
