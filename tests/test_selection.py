import pytest

from m_dlp.engine import TorrentFile
from m_dlp.selection import filter_files, parse_selection, parse_size


@pytest.mark.parametrize(
    ("expression", "expected"),
    [("1", {1}), ("1,3-5", {1, 3, 4, 5}), (" 2, 1-3,2 ", {1, 2, 3})],
)
def test_file_numbers_and_ranges(expression, expected):
    assert parse_selection(expression, {1, 2, 3, 4, 5}) == expected


@pytest.mark.parametrize("expression", ["", "0", "6", "2-1", "1,", "a", "-1", "1--2", "1 2"])
def test_invalid_selection_is_rejected(expression):
    with pytest.raises(ValueError):
        parse_selection(expression, {1, 2, 3, 4, 5})


def test_hidden_padding_indices_cannot_be_selected():
    with pytest.raises(ValueError):
        parse_selection("1-3", {1, 3})


def test_huge_ranges_are_rejected_without_expansion():
    with pytest.raises(ValueError):
        parse_selection("1-99999999999999999999999", {1, 2, 3})


FILES = [
    TorrentFile(1, "Movie/Film.MKV", 2048),
    TorrentFile(2, "Movie/sample.mkv", 256),
    TorrentFile(3, "Movie/subtitles/en.SRT", 64),
    TorrentFile(5, "Extras/other.mp4", 2048),
    TorrentFile(6, "empty.txt", 0),
]


def test_globs_extensions_and_sizes_intersect():
    assert filter_files(
        FILES,
        includes=["movie/*"],
        excludes=["*sample*"],
        extensions=[".mkv"],
        min_size=2048,
        max_size=2048,
    ) == {1}
    assert filter_files(FILES, includes=["*.srt", "empty.*"]) == {3, 6}


def test_filter_narrows_number_selection():
    assert filter_files(FILES, selected={2, 3, 5}, extensions=["MKV", "mp4"]) == {2, 5}


def test_largest_is_applied_last_with_stable_ties():
    assert filter_files(FILES, largest=True) == {1}
    assert filter_files(FILES, excludes=["film.*"], largest=True) == {5}


def test_no_matches_returns_empty_selection():
    assert filter_files(FILES, includes=["*.zip"]) == set()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", 0),
        ("1K", 1024),
        ("1.5 MiB", 1572864),
        ("2MB", 2000000),
        ("3gb", 3000000000),
        ("12 B", 12),
    ],
)
def test_parse_sizes(value, expected):
    assert parse_size(value) == expected


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "", "1XB", "0.1B"])
def test_invalid_sizes(value):
    with pytest.raises(ValueError):
        parse_size(value)
