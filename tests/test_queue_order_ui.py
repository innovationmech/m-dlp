from test_queue_order import ids
from test_queue_ui import until
from test_queue_ui_bulk import with_panel


def test_boundary_feedback_and_external_reorder_preserve_focused_id(tmp_path):
    async def scenario(panel, pipe, writer):
        pipe.send_text("K")
        await until(lambda: "边界" in panel.message)
        assert ids(writer) == [1, 2, 3]
        writer.set_priority([3], 2)
        await until(lambda: [r.id for r in panel.rows] == [3, 1, 2])
        assert panel.current.id == 1
        pipe.send_text("K")  # task 1 cannot cross the higher-priority group
        await until(lambda: "边界" in panel.message)
        assert ids(writer) == [3, 1, 2]

    with_panel(tmp_path, scenario)


def test_move_priority_and_pin_keys_preserve_focus_and_selection(tmp_path):
    async def scenario(panel, pipe, writer):
        pipe.send_text("\x1b[B K")  # select task 2 and move it above task 1
        await until(lambda: ids(writer) == [2, 1, 3])
        assert panel.current.id == 2 and panel.model.selected == {2}
        pipe.send_text("J")
        await until(lambda: ids(writer) == [1, 2, 3])
        assert panel.current.id == 2
        pipe.send_text("+")
        await until(lambda: writer.get(2).priority == 2)
        assert ids(writer) == [2, 1, 3]
        assert "高" in panel.detail()
        pipe.send_text("t")
        await until(lambda: writer.get(2).pinned)
        assert "置顶" in panel.detail()
        pipe.send_text("t-")
        await until(lambda: not writer.get(2).pinned and writer.get(2).priority == 1)
        assert ids(writer) == [1, 3, 2]  # joins tail of normal-priority group
        assert panel.current.id == 2 and panel.model.selected == {2}

    with_panel(tmp_path, scenario)


def test_filtered_multiselection_uses_full_queue_neighbors_and_hidden_ids(tmp_path):
    async def scenario(panel, pipe, writer):
        pipe.send_text("\x1b[F /Movie\r")  # select task 3, then hide it
        await until(lambda: panel.model.selected == {3} and len(panel.rows) == 2)
        pipe.send_text("K")
        await until(lambda: ids(writer) == [1, 3, 2])
        assert panel.model.selected == {3} and panel.model.hidden_selected == 1
        pipe.send_text("a+")
        await until(lambda: all(writer.get(i).priority == 2 for i in (1, 2, 3)))
        assert panel.model.selected == {1, 2, 3}
        pipe.send_text("t")
        await until(lambda: all(writer.get(i).pinned for i in (1, 2, 3)))
        assert ids(writer) == [1, 3, 2]
        pipe.send_text("nt")  # fallback targets only the focused visible task
        await until(lambda: not writer.get(panel.current.id).pinned)
        assert sum(row.pinned for row in writer.snapshot()) == 2

    with_panel(tmp_path, scenario)


def test_search_and_state_chooser_do_not_reorder_tasks(tmp_path):
    async def scenario(panel, pipe, writer):
        before = writer.snapshot()
        pipe.send_text("/JKt+-")
        await until(lambda: panel.model.query == "JKt+-")
        assert writer.snapshot() == before
        pipe.send_text("\x1b")
        await until(lambda: panel.model.query == "")
        pipe.send_text("f")
        await until(lambda: panel.choosing_state)
        pipe.send_text("JKt+-\x1b[F")
        await until(lambda: panel.filter_index == 8)
        assert writer.snapshot() == before
        pipe.send_text("\x1b")
        await until(lambda: not panel.choosing_state)

    with_panel(tmp_path, scenario)
