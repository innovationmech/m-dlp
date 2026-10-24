"""Search, filtering and stable selection for live queue snapshots."""


class QueueView:
    def __init__(self):
        self.records = []
        self.rows = []
        self.cursor = 0
        self.query = ""
        self.state = None
        self.selected = set()

    @property
    def current(self):
        return self.rows[self.cursor] if self.rows else None

    @property
    def hidden_selected(self):
        return len(self.selected - {row.id for row in self.rows})

    def update(self, records):
        self.records = list(records)
        self.selected.intersection_update(row.id for row in self.records)
        self._rebuild()

    def search(self, query):
        self.query = query
        self._rebuild()

    def filter_state(self, state):
        self.state = state
        self._rebuild()

    def _rebuild(self):
        focused = self.current.id if self.current else None
        query = self.query.casefold()
        self.rows = [
            row
            for row in self.records
            if (self.state is None or row.state == self.state)
            and (
                not query
                or any(
                    query in text.casefold()
                    for text in (str(row.id), row.name, row.key, row.destination)
                )
            )
        ]
        self.cursor = next(
            (i for i, row in enumerate(self.rows) if row.id == focused),
            min(self.cursor, max(0, len(self.rows) - 1)),
        )

    def move(self, amount):
        self.cursor = max(0, min(len(self.rows) - 1, self.cursor + amount))

    def toggle(self):
        if self.current:
            self.selected.symmetric_difference_update({self.current.id})

    def select_visible(self):
        self.selected.update(row.id for row in self.rows)

    def clear_selection(self):
        self.selected.clear()

    def invert_visible(self):
        self.selected.symmetric_difference_update(row.id for row in self.rows)

    def targets(self):
        if self.selected:
            return sorted(self.selected)
        return [self.current.id] if self.current else []
