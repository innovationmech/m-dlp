import sys
from types import SimpleNamespace

import pytest
from test_cli import invoke

from m_dlp import queue_cli, queue_ui


@pytest.mark.parametrize("args", [("tui",), ("run", "--interactive")])
def test_queue_panel_rejects_piped_input_before_creating_database(tmp_path, args):
    path = tmp_path / "must-not-exist.db"
    result = invoke("queue", *args, "--queue-db", str(path))
    assert result.returncode == 2
    assert "需要可交互终端" in result.stderr
    assert not path.exists()


def test_queue_panel_commands_use_config_jobs_and_database(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text('queue-db = "state/queue.db"\njobs = 2\n')
    captured = []
    monkeypatch.setattr(
        queue_ui, "run_panel", lambda path, **kwargs: captured.append((path, kwargs)) or 0
    )
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    console = queue_cli.Console
    monkeypatch.setattr(
        queue_cli, "Console", lambda **kwargs: console(force_terminal=True, **kwargs)
    )
    monkeypatch.setenv("TERM", "xterm-256color")
    assert queue_cli.run(["run", "--interactive", "--config", str(config)]) == 0
    assert captured[-1] == (tmp_path / "state/queue.db", {"jobs": 2})
    assert queue_cli.run(["tui", "--config", str(config)]) == 0
    assert captured[-1] == (tmp_path / "state/queue.db", {"jobs": None})
    assert queue_cli.run(["run", "--interactive", "--config", str(config), "-j", "4"]) == 0
    assert captured[-1][1] == {"jobs": 4}


def test_queue_panel_help_documents_entry_points():
    result = invoke("queue", "--help")
    assert result.returncode == 0 and "tui" in result.stdout
    result = invoke("queue", "run", "--help")
    assert result.returncode == 0 and "--interactive" in result.stdout
