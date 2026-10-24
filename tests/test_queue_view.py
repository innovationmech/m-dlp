from dataclasses import replace

from m_dlp.queue_store import TaskSnapshot
from m_dlp.queue_view import QueueView


def row(number, name, state="pending"):
    return TaskSnapshot(
        number, state, 0, name, f"{number:040x}", f"/downloads/{number}", 0, 100, 0, 0, ""
    )


ROWS = [row(1, "Movie One"), row(2, "Movie Two", "paused"), row(3, "中文 字幕", "failed")]


def test_search_and_state_filter_intersect_without_changing_global_rows():
    view = QueueView()
    view.update(ROWS)
    view.search("MOVIE")
    assert [r.id for r in view.rows] == [1, 2]
    view.filter_state("paused")
    assert [r.id for r in view.rows] == [2]
    assert view.records == ROWS
    view.search("中文")
    assert view.rows == [] and view.current is None and view.cursor == 0
    view.filter_state(None)
    assert view.current.id == 3
    for query in ("3", ROWS[2].key, "/DOWNLOADS/3", "中文 字幕"):
        view.search(query)
        assert [r.id for r in view.rows] == [3]


def test_selection_survives_filters_refresh_and_state_change():
    view = QueueView()
    view.update(ROWS)
    view.toggle()
    view.move(1)
    view.toggle()
    view.search("movie two")
    assert view.selected == {1, 2} and view.hidden_selected == 1
    view.filter_state("paused")
    view.update([ROWS[0], replace(ROWS[1], state="running"), ROWS[2], row(4, "new")])
    assert not view.rows and view.hidden_selected == 2
    assert view.targets() == [1, 2]
    view.clear_selection()
    assert view.targets() == []
    view.search("")
    view.filter_state(None)
    assert view.targets() == [1]


def test_select_visible_does_not_select_hidden_or_new_rows_and_prunes_removed_ids():
    view = QueueView()
    view.update(ROWS)
    view.search("movie")
    view.select_visible()
    assert view.selected == {1, 2}
    view.update([*ROWS, row(4, "movie four")])
    assert view.selected == {1, 2}
    view.move(2)
    view.toggle()
    assert view.selected == {1, 2, 4}
    view.update([ROWS[1], ROWS[2], row(4, "movie four")])
    assert view.selected == {2, 4} and view.current.id == 4
    view.clear_selection()
    assert not view.selected and view.targets() == [4]


def test_focus_tracks_id_on_refresh_and_clamps_after_filter_hides_current():
    view = QueueView()
    view.update(ROWS)
    view.move(1)
    view.update([row(0, "new"), *ROWS])
    assert view.current.id == 2 and view.cursor == 2
    view.filter_state("failed")
    assert view.current.id == 3 and view.cursor == 0
    view.move(100)
    assert view.current.id == 3
    view.search("no match")
    view.move(-100)
    view.toggle()
    view.select_visible()
    assert not view.selected and view.cursor == 0
