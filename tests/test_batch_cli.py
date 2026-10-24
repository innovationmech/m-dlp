import io
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import libtorrent as lt
import pytest
from conftest import LOCAL_SETTINGS
from rich.console import Console
from test_cli import invoke

from m_dlp import batch, cli
from m_dlp.cli import _parse_args


@pytest.fixture
def batch_seeds(tmp_path):
    session = lt.session(LOCAL_SETTINGS)
    handles = []
    seeds = []
    for index, data in enumerate([b"a" * 131072, b"b" * 196608]):
        root = tmp_path / f"seed-{index}"
        path = root / "shared/data.bin"
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        storage = lt.file_storage()
        storage.add_file("shared/data.bin", len(data))
        torrent = lt.create_torrent(storage, 16384, lt.create_torrent.v1_only)
        lt.set_piece_hashes(torrent, str(root))
        info = lt.torrent_info(torrent.generate())
        params = lt.add_torrent_params()
        params.ti = info
        params.save_path = str(root)
        params.flags &= ~(lt.torrent_flags.paused | lt.torrent_flags.auto_managed)
        handles.append(session.add_torrent(params))
        seeds.append(
            SimpleNamespace(
                magnet=lt.make_magnet_uri(info) + f"&x.pe=127.0.0.1:{session.listen_port()}",
                key=str(info.info_hashes().v1),
                data=data,
                session=session,
            )
        )
    deadline = time.monotonic() + 10
    while not all(handle.status().is_seeding for handle in handles):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    yield seeds
    session.pause()
    for handle in handles:
        session.remove_torrent(handle)


def write_batch(tmp_path, sources):
    path = tmp_path / "links.txt"
    path.write_text("\n".join(sources))
    return path


def test_cli_batch_download_isolates_names_and_resumes_offline(batch_seeds, tmp_path):
    path = write_batch(tmp_path, [s.magnet for s in batch_seeds])
    output = tmp_path / "downloads"
    result = invoke("-i", str(path), "-j", "2", "--all", "-o", str(output), timeout=30)
    assert result.returncode == 0, result.stderr
    for seed in batch_seeds:
        assert (output / seed.key / "shared/data.bin").read_bytes() == seed.data
    assert "成功 2" in result.stdout
    batch_seeds[0].session.pause()
    path.write_text("\n".join(s.magnet for s in reversed(batch_seeds)))
    result = invoke(
        "-i", str(path), "--resume", "-o", str(output), "--metadata-timeout", "0.1", timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert "成功 2" in result.stdout


def test_cli_batch_list_never_creates_download_directory(batch_seeds, tmp_path):
    path = write_batch(tmp_path, [s.magnet for s in batch_seeds] + [batch_seeds[0].magnet])
    output = tmp_path / "downloads"
    result = invoke("--batch-file", str(path), "--list", "-o", str(output), timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("shared/data.bin") == 2
    assert "跳过 1" in result.stdout
    assert not output.exists()


def test_cli_batch_failure_does_not_block_remaining_downloads(batch_seeds, tmp_path):
    path = write_batch(tmp_path, ["magnet:?xt=urn:btih:" + "a" * 40, batch_seeds[0].magnet])
    output = tmp_path / "downloads"
    result = invoke(
        "-i",
        str(path),
        "-j",
        "1",
        "--all",
        "-o",
        str(output),
        "--metadata-timeout",
        "10",
        timeout=40,
    )
    assert result.returncode == 1
    assert (output / batch_seeds[0].key / "shared/data.bin").exists(), (
        result.stdout,
        result.stderr,
    )
    assert (output / batch_seeds[0].key / "shared/data.bin").read_bytes() == batch_seeds[0].data
    assert "成功 1" in result.stdout and "失败 1" in result.stdout
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "options", [[], ["--diagnose"], ["--export-torrent", "x.torrent"], ["--all", "--jobs", "0"]]
)
def test_invalid_batch_modes_are_rejected(tmp_path, options):
    path = write_batch(tmp_path, ["magnet:?xt=urn:btih:" + "a" * 40])
    result = invoke("-i", str(path), *options)
    assert result.returncode == 2


def test_source_and_batch_file_are_exclusive(tmp_path):
    result = invoke("magnet:?xt=urn:btih:" + "a" * 40, "-i", str(tmp_path / "links.txt"), "--all")
    assert result.returncode == 2


def test_batch_preflight_validates_all_lines_before_network(seed, tmp_path):
    path = write_batch(tmp_path, [seed.magnet, "not a magnet"])
    output = tmp_path / "downloads"
    result = invoke("-i", str(path), "--all", "-o", str(output))
    assert result.returncode == 2
    assert "第 2 行" in result.stderr
    assert not output.exists()
    assert not (tmp_path / "cache").exists()


def test_jobs_can_be_configured_and_overridden(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("jobs = 2")
    args = _parse_args(["-i", "links.txt", "--all", "--config", str(config)])
    assert args.jobs == 2
    args = _parse_args(["-i", "links.txt", "--all", "--config", str(config), "--jobs", "1"])
    assert args.jobs == 1


def test_batch_destination_symlink_is_rejected(seed, tmp_path):
    path = write_batch(tmp_path, [seed.magnet])
    output = tmp_path / "downloads"
    outside = tmp_path / "outside"
    output.mkdir()
    outside.mkdir()
    key = str(lt.parse_magnet_uri(seed.magnet).info_hashes.v1)
    (output / key).symlink_to(outside, target_is_directory=True)
    result = invoke("-i", str(path), "--all", "-o", str(output))
    assert result.returncode == 1
    assert list(outside.iterdir()) == []


def test_sigint_stops_queue_with_pending_tasks(tmp_path):
    path = write_batch(tmp_path, ["magnet:?xt=urn:btih:" + c * 40 for c in "abc"])
    process = subprocess.Popen(
        [sys.executable, "-m", "m_dlp", "-i", str(path), "--all", "-j", "1"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "NO_COLOR": "1", "PYTHONUNBUFFERED": "1"},
    )
    prefix = ""
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 10
            while "[1] 开始" not in prefix:
                assert time.monotonic() < deadline
                assert selector.select(2), "CLI did not start the first task"
                line = process.stdout.readline()
                assert line, "CLI exited before starting"
                prefix += line
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 130, (prefix + stdout, stderr)
        assert "未开始 3" in stdout  # active work is now persisted back to pending
        assert "[2] 开始" not in prefix + stdout
        assert "Traceback" not in stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_sigint_during_started_display_preserves_summary(tmp_path, monkeypatch):
    path = write_batch(tmp_path, ["magnet:?xt=urn:btih:" + c * 40 for c in "ab"])
    loaded = batch.load_batch(path, tmp_path / "downloads")
    options = _parse_args(["-i", str(path), "--all", "-j", "1"])
    ready = threading.Event()
    output = io.StringIO()

    def worker(task, cancelled, emit, *, args):
        emit(batch.TaskEvent(task, "metadata", 0))
        ready.set()
        assert cancelled.wait(3)
        return batch.TaskResult(task, "cancelled")

    monkeypatch.setattr(cli, "_batch_worker", worker)
    sent = False

    def interrupt_at_delivery(frame, event, arg):
        nonlocal sent
        if (
            not sent
            and event == "line"
            and frame.f_code.co_name == "drain"
            and "event" in frame.f_locals
        ):
            sent = True
            assert ready.wait(3)
            signal.raise_signal(signal.SIGINT)
        return interrupt_at_delivery

    old_trace = sys.gettrace()
    old_handler = signal.getsignal(signal.SIGINT)
    try:
        sys.settrace(interrupt_at_delivery)
        code = cli._execute_batch(
            options, Console(file=output), Console(file=io.StringIO()), loaded
        )
    finally:
        sys.settrace(old_trace)
    assert sent
    assert code == 130
    assert "未开始 1" in output.getvalue()
    assert signal.getsignal(signal.SIGINT) == old_handler
