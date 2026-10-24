from m_dlp.engine import TorrentFile
from m_dlp.picker import SelectionModel, display_text

FILES = (
    TorrentFile(1, "Movie/Film.MKV", 2048),
    TorrentFile(3, "Movie/subs/中文.SRT", 64),
    TorrentFile(5, "Movie/sample.mkv", 256),
    TorrentFile(8, "Extras/bonus.mp4", 1024),
    TorrentFile(11, "empty.txt", 0),
)


def focus(model, key):
    model.cursor = next(i for i, row in enumerate(model.rows) if row.key == key)


def test_directory_toggle_recursive_tristate_and_exact_total():
    model = SelectionModel(FILES)
    focus(model, "f:1")
    model.toggle()
    assert model.selected == {1}
    assert model.selected_size == 2048
    focus(model, "d:Movie")
    assert model.mark(model.current) == "[-]"
    model.toggle()
    assert model.selected == {1, 3, 5}
    assert model.selected_size == 2368
    assert model.mark(model.current) == "[x]"
    model.toggle()
    assert model.selected_size == 0 and model.selected == set()
    focus(model, "f:11")
    model.toggle()
    assert model.selected == {11} and model.selected_size == 0


def test_search_preserves_hidden_selection_and_limits_directory_toggles():
    model = SelectionModel(FILES)
    focus(model, "f:8")
    model.toggle()
    model.search(".mKv")
    assert model.matches == {1, 5}
    assert model.hidden_selected == 1
    focus(model, "d:Movie")
    model.toggle()
    assert model.selected == {1, 5, 8}
    assert model.selected_size == 3328
    model.select_matches(False)
    assert model.selected == {8}
    model.search("MOVIE/subs")
    assert model.matches == {3}
    model.select_matches(True)
    assert model.selected == {3, 8}
    model.search("")
    assert model.hidden_selected == 0
    assert model.selected == {3, 8}


def test_collapse_navigation_and_search_restore_the_tree():
    model = SelectionModel(FILES)
    focus(model, "d:Movie")
    model.left()
    assert all(row.key != "f:1" for row in model.rows)
    model.toggle()  # collapsed children are still part of directory selection
    assert model.selected == {1, 3, 5}
    model.search("中文")
    assert any(row.key == "f:3" for row in model.rows)
    model.search("")
    assert all(row.key != "f:1" for row in model.rows)
    focus(model, "d:Movie")
    model.right()
    model.right()
    assert model.current.parent == "d:Movie"
    model.left()
    assert not model.is_expanded(model.current)
    model.left()
    assert model.current.key == "d:Movie"


def test_empty_matches_and_navigation_boundaries_are_safe():
    model = SelectionModel(FILES)
    model.move(-999)
    assert model.cursor == 0
    model.move(999)
    assert model.cursor == len(model.rows) - 1
    model.search("absent")
    assert model.rows == [] and model.current is None
    model.move(5)
    model.toggle()
    model.left()
    model.right()
    model.select_matches(True)
    assert model.selected == set()
    model.search("")
    assert model.current is not None


def test_control_characters_are_displayed_literally():
    value = "电影[title]\x1b[31m\n\t\r\x00\u202e.mkv"
    rendered = display_text(value)
    assert rendered.startswith("电影[title]")
    assert "\\x1b" in rendered and "\\n" in rendered
    assert all(c.isprintable() for c in rendered)


def test_large_flat_tree_keeps_original_file_numbers_and_totals():
    files = [TorrentFile(i * 2 + 1, f"large/file-{i:05}.bin", i) for i in range(10000)]
    model = SelectionModel(files)
    model.select_matches(True)
    assert len(model.selected) == 10000
    assert model.selected_size == sum(range(10000))
    model.search("file-09999")
    assert model.matches == {19999}
    assert model.hidden_selected == 9999
