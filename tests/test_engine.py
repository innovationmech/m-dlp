import time
from types import SimpleNamespace

import libtorrent as lt
import pytest

from m_dlp.engine import (
    DownloadCancelled,
    DownloadError,
    DownloadStalled,
    MagnetDownload,
    MetadataTimeout,
)


def test_metadata_then_only_selected_file(seed, local_engine, tmp_path):
    target = tmp_path / "downloads"
    updates = []
    # The URI's so=0 asks for file 1, but must not download it before the
    # user's explicit selection of file 2 below.
    with MagnetDownload(seed.magnet + "&so=0") as download:
        files = download.resolve(timeout=10)
        assert [(f.number, f.path, f.size) for f in files] == [
            (1, "sample/first.bin", 32771),
            (2, "sample/second.bin", 65539),
            (3, "sample/empty.txt", 0),
        ]
        # A peer is already present; waiting at the selection prompt must not
        # start downloading torrent contents.
        time.sleep(0.2)
        assert download.handle.status().total_payload_download == 0
        assert not target.exists()
        download.download({2}, target, stall_timeout=10, on_progress=updates.append)
    assert (target / "sample/second.bin").read_bytes() == b"b" * 65539
    assert not (target / "sample/first.bin").exists()
    assert updates[-1].completed == updates[-1].total == 65539


def test_selected_empty_file_is_created(seed, local_engine, tmp_path):
    target = tmp_path / "downloads"
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
        download.download({3}, target, stall_timeout=10)
    assert (target / "sample/empty.txt").read_bytes() == b""
    assert not (target / "sample/first.bin").exists()


@pytest.mark.parametrize("resume", [False, True])
def test_download_reuses_discovered_peers_without_rediscovery(seed, local_engine, tmp_path, resume):
    target = tmp_path / "downloads"
    if resume:
        with MagnetDownload(seed.magnet) as download:
            download.resolve(timeout=10)
            download.download({1}, target, stall_timeout=10)

    # This endpoint stands in for a peer discovered via DHT/PEX/tracker. It
    # isn't in the source, and no discovery service can supply it again.
    magnet = lt.make_magnet_uri(lt.torrent_info(str(seed.torrent_path)))
    with MagnetDownload(magnet) as download:
        download.handle.connect_peer(("127.0.0.1", seed.session.listen_port()))
        download.resolve(timeout=10)
        deadline = time.monotonic() + 10
        while not download.handle.status().num_peers:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert download.handle.status().total_payload_download == 0
        if resume:
            assert download.saved_selection(target) == {1}
        download.download({2}, target, stall_timeout=3)
    assert (target / "sample/second.bin").read_bytes() == seed.contents["sample/second.bin"]


def test_existing_file_is_verified_and_repaired(seed, local_engine, tmp_path):
    target = tmp_path / "downloads"
    file = target / "sample/second.bin"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"x" * 65539)
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
        download.download({2}, target, stall_timeout=10)
    assert file.read_bytes() == b"b" * 65539


def test_metadata_timeout_without_peers(local_engine):
    with MagnetDownload("magnet:?xt=urn:btih:" + "a" * 40) as download:
        with pytest.raises(MetadataTimeout):
            download.resolve(timeout=0.1)


@pytest.mark.parametrize(
    "magnet", ["", "https://example.org/file", "magnet:?dn=test", "magnet:?xt=urn:btih:bad"]
)
def test_invalid_magnets_fail_before_starting_download(magnet):
    with pytest.raises(ValueError):
        MagnetDownload(magnet)


def test_empty_selection_does_not_create_output(seed, local_engine, tmp_path):
    target = tmp_path / "downloads"
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
        with pytest.raises(ValueError):
            download.download(set(), target)
    assert not target.exists()


def test_stalled_download_reports_failure(seed, local_engine, tmp_path):
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
        seed.session.pause()
        with pytest.raises(DownloadStalled):
            download.download({2}, tmp_path / "downloads", stall_timeout=0.2)


def test_alert_bursts_do_not_flood_progress_callbacks(seed, local_engine, tmp_path):
    updates = []
    started = None

    def cancelled():
        return started is not None and time.monotonic() - started >= 0.6

    with MagnetDownload(seed.magnet, cancelled=cancelled) as download:
        download.resolve(timeout=10)
        seed.session.pause()

        def update(progress):
            nonlocal started
            started = started or time.monotonic()
            updates.append(progress)
            # Real alerts wake wait_for_alert immediately. Progress sampling
            # must have its own cadence even when notifications keep arriving.
            download.session.post_session_stats()

        with pytest.raises(DownloadCancelled):
            download.download({2}, tmp_path / "downloads", on_progress=update)

    assert updates
    assert len(updates) <= 5


def test_progress_does_not_copy_piece_maps(seed, local_engine, tmp_path, monkeypatch):
    original_status = lt.torrent_handle.status
    copied_maps = []

    def status(handle, *args, **kwargs):
        result = original_status(handle, *args, **kwargs)
        if result.pieces or result.verified_pieces:
            copied_maps.append(result)
        return result

    monkeypatch.setattr(lt.torrent_handle, "status", status)
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
        download.download({2}, tmp_path / "downloads", stall_timeout=10)
        assert not copied_maps
        # Diagnostics still need the actual piece map to report local coverage.
        snapshot = download.diagnostics({2})
        assert snapshot.files[0].local_bytes == 65539
        assert copied_maps


def test_output_symlink_cannot_redirect_writes(seed, local_engine, tmp_path):
    target = tmp_path / "downloads"
    outside = tmp_path / "outside"
    target.mkdir()
    outside.mkdir()
    (target / "sample").symlink_to(outside, target_is_directory=True)
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
        with pytest.raises(DownloadError):
            download.download({2}, target)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("seed", [lt.create_torrent.v2_only, 0], indirect=True)
def test_v2_and_hybrid_magnets(seed, local_engine, tmp_path):
    target = tmp_path / "downloads"
    with MagnetDownload(seed.magnet) as download:
        files = download.resolve(timeout=10)
        chosen = next(file for file in files if file.path == "sample/second.bin")
        download.download({chosen.number}, target, stall_timeout=10)
    assert (target / "sample/second.bin").read_bytes() == b"b" * 65539
    assert not (target / "sample/first.bin").exists()


def test_interrupt_preserves_data_for_restart(seed, local_engine, tmp_path):
    target = tmp_path / "downloads"

    def interrupt(snapshot):
        if snapshot.completed:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt), MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
        download.download({2}, target, stall_timeout=10, on_progress=interrupt)
    assert (target / "sample/second.bin").exists()
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
        assert download.saved_selection(target) == {2}
        download.download({2}, target, stall_timeout=10)
        assert download.resume_loaded
    assert (target / "sample/second.bin").read_bytes() == b"b" * 65539


def test_wait_for_removal_does_not_query_a_handle_being_invalidated(monkeypatch):
    # remove_torrent is asynchronous. is_valid can succeed while status,
    # queued behind removal on the native thread, throws invalid_handle.
    class RemovingHandle:
        def is_valid(self):
            return True

        def status(self):
            raise RuntimeError("invalid torrent handle used [libtorrent:20]")

    class RemovedAlert:
        pass

    monkeypatch.setattr(lt, "torrent_removed_alert", RemovedAlert)
    download = MagnetDownload("magnet:?xt=urn:btih:" + "a" * 40)
    download.handle = RemovingHandle()
    alert = RemovedAlert()
    alert.handle = download.handle
    pending = iter([[], [alert]])
    download.session = SimpleNamespace(
        pop_alerts=lambda: next(pending), wait_for_alert=lambda milliseconds: None
    )
    download._wait_for(lt.torrent_removed_alert, "切换下载任务")
