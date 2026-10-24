"""Shared terminal styling, column sizing and keyboard-accessible reading views."""

from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.key_binding.bindings.scroll import scroll_page_down, scroll_page_up
from prompt_toolkit.layout import ConditionalContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl, UIContent
from prompt_toolkit.layout.margins import Margin
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import TextArea

from .picker import display_text

STYLE = Style.from_dict(
    {
        # Unpainted cells use the terminal background too; never mix RGB panel
        # fills with them. ANSI foregrounds also respect the terminal palette.
        # Namespace roles to avoid built-in search/selected background styles.
        "": "fg:default bg:default",
        "mdlp.title": "ansicyan bold",
        "mdlp.subtitle": "",
        "mdlp.header": "bold",
        "mdlp.query": "ansibrightblack",
        "mdlp.query.focused": "ansicyan bold",
        "mdlp.cursor": "ansicyan bold",
        "mdlp.directory": "ansicyan",
        "mdlp.selected": "ansigreen bold",
        "mdlp.muted": "ansibrightblack",
        "mdlp.status": "",
        "mdlp.hint": "ansibrightblack",
        "mdlp.notice": "ansiyellow",
        "mdlp.running": "ansicyan",
        "mdlp.completed": "ansigreen",
        "mdlp.failed": "ansired bold",
        "mdlp.paused": "ansiyellow",
        "mdlp.pausing": "ansiyellow",
        "mdlp.cancelling": "ansiyellow",
        "mdlp.cancelled": "ansibrightblack",
        "mdlp.priority": "ansimagenta",
        "mdlp.separator": "ansibrightblack",
        "mdlp.scroll.track": "ansibrightblack",
        "mdlp.scroll.thumb": "ansicyan",
    }
)


def cell(text, width, *, right=False):
    """Fit safe text to terminal columns, making truncation explicit."""
    width = max(0, width)
    text = display_text(str(text))
    if get_cwidth(text) > width:
        value, used = "", 0
        for char in text:
            size = max(0, get_cwidth(char))
            if used + size > width - 1:
                break
            value += char
            used += size
        text = value + "…" if width else ""
    padding = " " * max(0, width - get_cwidth(text))
    return padding + text if right else text + padding


def column_cell(text, width, *, right=False, gap=2):
    """Keep an explicit gutter even when a value fills its column."""
    gap = min(gap, max(0, width))
    return cell(text, max(0, width - gap), right=right) + " " * gap


class SpaceMargin(Margin):
    def get_width(self, get_ui_content):
        return 1

    def create_margin(self, window_render_info, width, height):
        return [("", "\n".join(" " * width for _ in range(height)))]


class ScrollIndicator(Margin):
    """A thin foreground-only indicator, hidden when all content fits."""

    def get_width(self, get_ui_content):
        return 1

    def create_margin(self, info, width, height):
        if height <= 0:
            return []
        if info.wrap_lines:
            line_heights = [info.get_height_for_line(i) for i in range(info.content_height)]
            total = sum(line_heights)
            offset = sum(line_heights[: info.vertical_scroll]) + info.window.vertical_scroll_2
        else:
            total = info.content_height
            offset = info.vertical_scroll
        if total <= height:
            return []
        thumb = max(1, height * height // total)
        top = min(height - thumb, max(0, round((height - thumb) * offset / (total - height))))
        fragments = []
        for y in range(height):
            active = top <= y < top + thumb
            fragments.append(
                (
                    "class:mdlp.scroll.thumb" if active else "class:mdlp.scroll.track",
                    "┃" if active else "│",
                )
            )
            if y < height - 1:
                fragments.append(("", "\n"))
        return fragments


def empty_content(width, height, title, description, action):
    """Center a short empty state without creating a fake scrollable list."""
    height = max(1, height)
    lines = [("class:mdlp.title", title), ("", ""), ("class:mdlp.muted", description), ("", action)]
    if height < 4:
        lines = [lines[0], lines[-1]][:height]
    top = max(0, (height - len(lines)) // 2)

    def line(index):
        if not top <= index < top + len(lines):
            return [("", "")]
        style, text = lines[index - top]
        padding = " " * max(0, (width - get_cwidth(text)) // 2)
        return [(style, cell(padding + text, width))]

    return UIContent(
        get_line=line, line_count=height, cursor_position=Point(0, 0), show_cursor=False
    )


def padded_window(*args, **kwargs):
    right_margins = kwargs.pop("right_margins", [])
    return Window(
        *args,
        left_margins=[SpaceMargin()],
        right_margins=[SpaceMargin(), *right_margins],
        **kwargs,
    )


def pad_text_area(area):
    area.window.left_margins.insert(0, SpaceMargin())
    area.window.right_margins.insert(0, SpaceMargin())


class Spacing:
    def __init__(self, get_size):
        self.get_size = get_size

    @property
    def side(self):
        width = self.get_size().columns
        return 2 if width >= 80 else 1 if width >= 40 else 0

    @property
    def table_width(self):
        # Outer gutters, both inner margins, and one scrollbar column.
        return max(0, self.get_size().columns - 2 * self.side - 3)

    def gap(self, min_rows=24):
        return ConditionalContainer(
            Window(height=1), filter=Condition(lambda: self.get_size().rows >= min_rows)
        )

    def rule(self, min_rows=24):
        return ConditionalContainer(
            padded_window(height=1, char="─", style="class:mdlp.separator"),
            filter=Condition(lambda: self.get_size().rows >= min_rows),
        )

    def frame(self, content):
        return HSplit(
            [
                self.gap(18),
                VSplit([Window(width=lambda: self.side), content, Window(width=lambda: self.side)]),
                self.gap(18),
            ]
        )


class ReadingView:
    """A full-screen, scrollable help/detail view that restores its caller's focus."""

    def __init__(self, spacing):
        self.visible = False
        self.title = ""
        self.previous = None
        self.body = TextArea(read_only=True, wrap_lines=True)
        self.body.window.right_margins = [ScrollIndicator()]
        pad_text_area(self.body)
        self.container = HSplit(
            [
                padded_window(
                    FormattedTextControl(lambda: self.title), height=1, style="class:mdlp.title"
                ),
                spacing.rule(18),
                self.body,
                spacing.rule(18),
                padded_window(
                    FormattedTextControl("Esc / Enter / q 返回 · ↑↓ 滚动 · PgUp/PgDn 翻页"),
                    height=1,
                    style="class:mdlp.status",
                ),
            ]
        )

    def wrap(self, content):
        return HSplit(
            [
                ConditionalContainer(content, filter=Condition(lambda: not self.visible)),
                ConditionalContainer(self.container, filter=Condition(lambda: self.visible)),
            ]
        )

    def open(self, app, title, text):
        self.previous = app.layout.current_control
        self.title = title
        self.body.text = text
        self.body.window.vertical_scroll = 0
        self.visible = True
        app.layout.focus(self.body)

    def close(self, app):
        self.visible = False
        app.layout.focus(self.previous)

    def bind(self, bindings):
        focused = has_focus(self.body)

        @bindings.add("escape", filter=focused)
        @bindings.add("enter", filter=focused)
        @bindings.add("q", filter=focused)
        @bindings.add("?", filter=focused)
        @bindings.add("f1", filter=focused)
        def close(event):
            self.close(event.app)

        @bindings.add("home", filter=focused)
        def first(event):
            self.body.buffer.cursor_position = 0

        @bindings.add("end", filter=focused)
        def last(event):
            self.body.buffer.cursor_position = len(self.body.text)

        @bindings.add("pageup", filter=focused)
        def page_up(event):
            scroll_page_up(event)

        @bindings.add("pagedown", filter=focused)
        def page_down(event):
            scroll_page_down(event)

        @bindings.add("k", filter=focused)
        def up(event):
            self.body.buffer.cursor_up()

        @bindings.add("j", filter=focused)
        def down(event):
            self.body.buffer.cursor_down()
