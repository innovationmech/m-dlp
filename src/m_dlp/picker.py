"""Searchable file-tree selection, independent of terminal and download state."""

import os
from dataclasses import dataclass, field


def display_text(value: str) -> str:
    """Render torrent-controlled control characters as visible literal escapes."""
    return "".join(c if c.isprintable() else c.encode("unicode_escape").decode() for c in value)


@dataclass
class Entry:
    key: str
    name: str
    path: str
    parent: str | None
    depth: int
    directory: bool
    members: set[int] = field(default_factory=set)
    children: list[str] = field(default_factory=list)


class SelectionModel:
    def __init__(self, files):
        self.files = {file.number: file for file in files}
        self.entries = {"d:": Entry("d:", "", "", None, -1, True)}
        self.selected: set[int] = set()
        self.query = ""
        self.matches = set(self.files)
        self.collapsed: set[str] = set()
        self.search_collapsed: set[str] = set()
        self.rows: list[Entry] = []
        self.cursor = 0
        for number, file in self.files.items():
            parts = file.path.replace(os.sep, "/").split("/")
            parent = self.entries["d:"]
            parent.members.add(number)
            for depth, part in enumerate(parts[:-1]):
                path = "/".join(parts[: depth + 1])
                key = "d:" + path
                if key not in self.entries:
                    self.entries[key] = Entry(key, part, path, parent.key, depth, True)
                    parent.children.append(key)
                parent = self.entries[key]
                parent.members.add(number)
            key = f"f:{number}"
            self.entries[key] = Entry(
                key, parts[-1], file.path, parent.key, len(parts) - 1, False, {number}
            )
            parent.children.append(key)
        for entry in self.entries.values():
            entry.children.sort(
                key=lambda key: (
                    not self.entries[key].directory,
                    self.entries[key].name.casefold(),
                    key,
                )
            )
        self._rebuild()

    @property
    def current(self):
        return self.rows[self.cursor] if self.rows else None

    @property
    def selected_size(self) -> int:
        return sum(self.files[number].size for number in self.selected)

    @property
    def hidden_selected(self) -> int:
        return len(self.selected - self.matches)

    def is_expanded(self, entry) -> bool:
        collapsed = self.search_collapsed if self.query else self.collapsed
        return entry.key not in collapsed

    def _rebuild(self):
        focused = self.current.key if self.current else None
        rows = []
        stack = list(reversed(self.entries["d:"].children))
        while stack:
            entry = self.entries[stack.pop()]
            if not entry.members & self.matches:
                continue
            rows.append(entry)
            if entry.directory and self.is_expanded(entry):
                stack.extend(reversed(entry.children))
        self.rows = rows
        self.cursor = next((i for i, entry in enumerate(rows) if entry.key == focused), 0)

    def search(self, query: str):
        self.query = query
        self.matches = {
            number
            for number, file in self.files.items()
            if query.casefold() in file.path.casefold()
        }
        self.search_collapsed.clear()
        self._rebuild()

    def move(self, delta: int):
        self.cursor = max(0, min(self.cursor + delta, len(self.rows) - 1))

    def left(self):
        entry = self.current
        if entry is None:
            return
        if entry.directory and self.is_expanded(entry):
            collapsed = self.search_collapsed if self.query else self.collapsed
            collapsed.add(entry.key)
            self._rebuild()
        else:
            self.cursor = next(
                (i for i, row in enumerate(self.rows) if row.key == entry.parent), self.cursor
            )

    def right(self):
        entry = self.current
        if entry is None or not entry.directory:
            return
        if self.is_expanded(entry):
            self.move(1)
        else:
            collapsed = self.search_collapsed if self.query else self.collapsed
            collapsed.discard(entry.key)
            self._rebuild()

    def members(self, entry) -> set[int]:
        return entry.members & self.matches

    def mark(self, entry) -> str:
        members = self.members(entry)
        selected = members & self.selected
        return "[x]" if selected == members else "[-]" if selected else "[ ]"

    def toggle(self):
        if self.current is not None:
            members = self.members(self.current)
            if members <= self.selected:
                self.selected.difference_update(members)
            else:
                self.selected.update(members)

    def select_matches(self, selected: bool):
        if selected:
            self.selected.update(self.matches)
        else:
            self.selected.difference_update(self.matches)

    def invert_matches(self):
        self.selected.symmetric_difference_update(self.matches)
