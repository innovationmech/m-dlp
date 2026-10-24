import io
import os
import sys

import pytest
from rich.console import Console
from test_cli import invoke

from m_dlp import cli
from m_dlp.engine import TorrentFile

FILES = (TorrentFile(1, "sample/first.bin", 50), TorrentFile(3, "sample/second.bin", 150))


def test_default_terminal_uses_tree_and_keeps_explicit_selection_authoritative(monkeypatch):
    from m_dlp import picker_ui

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setenv("TERM", "xterm-256color")
    calls = []
    monkeypatch.setattr(picker_ui, "choose_files", lambda files, name: calls.append(files) or {3})
    args = cli._parse_args(["input.torrent"])
    console = Console(file=io.StringIO(), force_terminal=True)
    assert cli._prompt_files(args, console, "sample", FILES) == {3}
    assert calls == [FILES]
    assert cli._wants_interactive(args)
    assert not cli._wants_interactive(cli._parse_args(["input.torrent", "--all"]))
    assert not cli._wants_interactive(cli._parse_args(["input.torrent", "--ext", "bin"]))
    assert cli._wants_interactive(
        cli._parse_args(["input.torrent", "--ext", "bin", "--interactive"])
    )


@pytest.mark.parametrize("mode", ["dumb", "redirected", "simple"])
def test_numeric_fallback_without_fullscreen_output(monkeypatch, mode):
    from m_dlp import picker_ui

    monkeypatch.setattr(sys, "stdin", io.StringIO("3\n"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setenv("TERM", "dumb" if mode == "dumb" else "xterm")

    def unexpected(*args):
        pytest.fail("Numeric fallback must not enter full-screen mode")

    monkeypatch.setattr(picker_ui, "choose_files", unexpected)
    options = ["--simple-select"] if mode == "simple" else []
    args = cli._parse_args(["input.torrent", *options])
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=mode != "redirected")
    assert cli._prompt_files(args, console, "sample", FILES) == {3}
    assert "second.bin" in stream.getvalue()
    assert "\x1b[?1049" not in stream.getvalue()


@pytest.mark.parametrize("flag", ["--interactive", "--simple-select"])
def test_interaction_rejects_pipes_batches_and_conflicting_modes(flag, tmp_path, monkeypatch):
    result = invoke("input.torrent", flag)
    assert result.returncode == 2 and "终端" in result.stderr
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    for args in (
        ["-i", "links.txt", flag],
        ["input.torrent", flag, "--diagnose"],
        ["input.torrent", flag, "--all"],
        ["input.torrent", flag, "--list"],
    ):
        with pytest.raises(SystemExit) as exc:
            cli.run(args)
        assert exc.value.code == 2
    assert not (tmp_path / "cache").exists()
    assert not (tmp_path / "state").exists()


def test_queue_interactive_selection_saves_explicit_numbers_without_downloading(
    seed, tmp_path, monkeypatch
):
    from m_dlp.queue_store import QueueStore

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    offered = []

    def select(args, console, name, files):
        offered.extend(files)
        return {2}

    monkeypatch.setattr(cli, "_prompt_files", select)
    output = tmp_path / "downloads"
    assert (
        cli.run(
            [
                "queue",
                "add",
                str(seed.torrent_path),
                "--interactive",
                "--ext",
                "bin",
                "-o",
                str(output),
            ]
        )
        == 0
    )
    assert [file.number for file in offered] == [1, 2]
    assert not output.exists()
    with QueueStore() as store:
        records = store.list()
        assert len(records) == 1
        assert records[0].options["select"] == "2"
        assert records[0].state == "pending"


def test_cancel_queue_selection_creates_no_record(seed, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "_prompt_files", lambda *args: set())
    assert cli.run(["queue", "add", str(seed.torrent_path), "--interactive"]) == 0
    assert not os.path.exists(os.environ["M_DLP_QUEUE_DB"])
