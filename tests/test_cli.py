import io
import os
import subprocess
import sys

import pytest
from rich.console import Console

from m_dlp.cli import _choose_files


def invoke(*args, timeout=20):
    return subprocess.run(
        [sys.executable, "-m", "m_dlp", *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "NO_COLOR": "1", "COLUMNS": "120"},
    )


def test_help():
    result = invoke("--help")
    assert result.returncode == 0
    assert "--select" in result.stdout


def test_noninteractive_requires_explicit_selection():
    result = invoke("magnet:?xt=urn:btih:" + "a" * 40)
    assert result.returncode == 2
    assert "--select" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ("--metadata-timeout", "0"),
        ("--metadata-timeout", "nan"),
        ("--stall-timeout", "-1"),
        ("--all", "--list"),
    ],
)
def test_invalid_options(args):
    result = invoke("magnet:?xt=urn:btih:" + "a" * 40, *args)
    assert result.returncode == 2


def test_list_then_select_via_real_cli(seed, tmp_path):
    target = tmp_path / "downloads"
    result = invoke(seed.magnet, "--list", "-o", str(target), "--metadata-timeout", "10")
    assert result.returncode == 0, result.stderr
    assert "sample/second.bin" in result.stdout
    assert not target.exists()
    result = invoke(
        seed.magnet,
        "--select",
        "2",
        "-o",
        str(target),
        "--metadata-timeout",
        "10",
        "--stall-timeout",
        "10",
    )
    assert result.returncode == 0, result.stderr
    assert (target / "sample/second.bin").read_bytes() == b"b" * 65539
    assert not (target / "sample/first.bin").exists()


def test_all_files_via_real_cli(seed, tmp_path):
    target = tmp_path / "downloads"
    result = invoke(seed.magnet, "--all", "-o", str(target), "--metadata-timeout", "10")
    assert result.returncode == 0, result.stderr
    for name, contents in seed.contents.items():
        assert (target / name).read_bytes() == contents


def test_prompt_retries_invalid_selection(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("99\n1,3\n"))
    assert _choose_files(Console(file=io.StringIO()), {1, 2, 3}) == {1, 3}


def test_prompt_can_cancel(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("q\n"))
    assert _choose_files(Console(file=io.StringIO()), {1, 2, 3}) == set()


def test_filter_downloads_largest_matching_file(seed, tmp_path):
    target = tmp_path / "downloads"
    result = invoke(
        seed.magnet, "--include", "*.BIN", "--min-size", "32K", "--largest", "-o", str(target)
    )
    assert result.returncode == 0, result.stderr
    assert (target / "sample/second.bin").read_bytes() == b"b" * 65539
    assert not (target / "sample/first.bin").exists()


def test_filters_with_no_matches_are_errors(seed, tmp_path):
    target = tmp_path / "downloads"
    result = invoke(seed.magnet, "--ext", "zip", "-o", str(target))
    assert result.returncode == 2
    assert "没有" in result.stderr
    assert not target.exists()


def test_filtered_listing_preserves_original_numbers(seed):
    result = invoke(seed.magnet, "--list", "--exclude", "*.bin")
    assert result.returncode == 0, result.stderr
    assert "empty.txt" in result.stdout
    assert "second.bin" not in result.stdout


def test_diagnose_does_not_create_output(seed, tmp_path):
    target = tmp_path / "downloads"
    result = invoke(
        seed.magnet, "--diagnose", "--select", "2", "--probe-seconds", "0.5", "-o", str(target)
    )
    assert result.returncode == 0, result.stderr
    assert "DHT" in result.stdout
    assert "覆盖" in result.stdout
    assert "保证" in result.stdout
    assert not target.exists()


def test_cli_can_resume_saved_selection_offline(seed, tmp_path):
    target = tmp_path / "downloads"
    result = invoke(seed.magnet, "--select", "2", "-o", str(target))
    assert result.returncode == 0, result.stderr
    seed.session.pause()
    result = invoke(
        seed.magnet,
        "--resume",
        "-o",
        str(target),
        "--metadata-timeout",
        "0.2",
        "--stall-timeout",
        "1",
    )
    assert result.returncode == 0, result.stderr
    assert "缓存" in result.stdout
    assert "快速恢复" in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        ("--resume", "--no-cache"),
        ("--all", "--min-size", "2M", "--max-size", "1M"),
        ("--diagnose", "--probe-seconds", "nan"),
        ("--all", "--ext", ""),
        ("--all", "--include", ""),
        ("--list", "--diagnose"),
    ],
)
def test_new_invalid_options_are_rejected_without_network(args):
    result = invoke("magnet:?xt=urn:btih:" + "a" * 40, *args)
    assert result.returncode == 2


def test_metadata_failure_prints_diagnostics():
    result = invoke("magnet:?xt=urn:btih:" + "a" * 40, "--diagnose", "--metadata-timeout", "0.1")
    assert result.returncode == 1
    assert "下载诊断" in result.stderr
    assert "DHT" in result.stderr
    assert "Traceback" not in result.stderr
