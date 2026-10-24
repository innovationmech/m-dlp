import os

import libtorrent as lt
import pytest

from m_dlp.engine import MagnetDownload


@pytest.mark.parametrize(
    "seed", [lt.create_torrent.v1_only, lt.create_torrent.v2_only, 0], indirect=True
)
def test_metadata_can_be_listed_offline(seed, local_engine):
    with MagnetDownload(seed.magnet) as download:
        expected = download.resolve(timeout=10)
    seed.session.pause()
    with MagnetDownload(seed.magnet) as download:
        assert download.resolve(timeout=0.2) == expected
        assert download.metadata_cached
        assert download.handle.status().total_payload_download == 0


def test_resume_reuses_verified_data_and_saved_selection(seed, local_engine, tmp_path):
    target = tmp_path / "downloads"
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
        download.download({2}, target, stall_timeout=10)
    seed.session.pause()
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=0.2)
        assert download.saved_selection(target) == {2}
        assert download.saved_selection(tmp_path / "elsewhere") is None
        download.download({2}, target, stall_timeout=1)
        assert download.resume_loaded
        assert download.handle.status().total_payload_download == 0
    assert (target / "sample/second.bin").read_bytes() == b"b" * 65539


def test_changed_file_invalidates_resume_and_is_repaired(seed, local_engine, tmp_path):
    target = tmp_path / "downloads"
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
        download.download({2}, target, stall_timeout=10)
    (target / "sample/second.bin").write_bytes(b"x" * 65539)
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=1)
        download.download({2}, target, stall_timeout=10)
        assert not download.resume_loaded
    assert (target / "sample/second.bin").read_bytes() == b"b" * 65539


def test_resume_selection_can_change(seed, local_engine, tmp_path):
    target = tmp_path / "downloads"
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
        download.download({2}, target, stall_timeout=10)
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=1)
        download.download({1}, target, stall_timeout=10)
    assert (target / "sample/first.bin").read_bytes() == b"a" * 32771
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=1)
        assert download.saved_selection(target) == {1}


def test_corrupt_resume_falls_back_to_file_check(seed, local_engine, tmp_path):
    target = tmp_path / "downloads"
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
        download.download({2}, target, stall_timeout=10)
    for path in (tmp_path / "cache").rglob("*.json"):
        path.write_text("not json")
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=1)
        download.download({2}, target, stall_timeout=10)
        assert not download.resume_loaded
        assert download.warnings


def test_no_cache_leaves_no_cache_files(seed, local_engine, tmp_path):
    with MagnetDownload(seed.magnet, use_cache=False) as download:
        download.resolve(timeout=10)
    assert not (tmp_path / "cache").exists()


def test_recheck_bypasses_fast_resume(seed, local_engine, tmp_path):
    target = tmp_path / "downloads"
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
        download.download({2}, target, stall_timeout=10)
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=1)
        download.download({2}, target, stall_timeout=10, recheck=True)
        assert not download.resume_loaded


def test_cached_metadata_cannot_add_network_sources(seed, local_engine, tmp_path):
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
    for path in (tmp_path / "cache").glob("*.torrent"):
        data = lt.bdecode(path.read_bytes())
        data[b"announce"] = b"http://127.0.0.1:1/private/announce"
        data[b"url-list"] = [b"http://127.0.0.1:1/files"]
        path.write_bytes(lt.bencode(data))
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=1)
        assert download.handle.trackers() == []
        assert list(download.handle.url_seeds()) == []


def test_file_replacement_with_preserved_mtime_invalidates_resume(seed, local_engine, tmp_path):
    target = tmp_path / "downloads"
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
        download.download({2}, target, stall_timeout=10)
    path = target / "sample/second.bin"
    stat = path.stat()
    path.unlink()
    path.write_bytes(b"x" * 65539)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=1)
        download.download({2}, target, stall_timeout=10)
        assert not download.resume_loaded
    assert path.read_bytes() == b"b" * 65539


def test_periodic_checkpoint_is_saved_before_context_closes(
    seed, local_engine, tmp_path, monkeypatch
):
    monkeypatch.setattr(local_engine, "CHECKPOINT_INTERVAL", 0)
    target = tmp_path / "downloads"
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
        download.download({2}, target, stall_timeout=10)
        assert list((tmp_path / "cache").rglob("*.json"))
        assert download.saved_selection(target) == {2}


@pytest.mark.parametrize("corruption", ["malformed", "wrong_hash"])
def test_invalid_metadata_cache_is_refetched(seed, local_engine, tmp_path, corruption):
    with MagnetDownload(seed.magnet) as download:
        expected = download.resolve(timeout=10)
    for path in (tmp_path / "cache").glob("*.torrent"):
        if corruption == "malformed":
            path.write_bytes(b"not bencode")
        else:
            data = lt.bdecode(path.read_bytes())
            data[b"info"][b"name"] = b"different torrent"
            path.write_bytes(lt.bencode(data))
    with MagnetDownload(seed.magnet) as download:
        assert download.resolve(timeout=10) == expected
        assert not download.metadata_cached
        assert download.warnings


def test_unwritable_cache_does_not_block_download(seed, local_engine, tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("keep me")
    target = tmp_path / "downloads"
    with MagnetDownload(seed.magnet, cache_dir=blocked) as download:
        download.resolve(timeout=10)
        download.download({2}, target, stall_timeout=10)
        assert download.warnings
    assert blocked.read_text() == "keep me"
    assert (target / "sample/second.bin").read_bytes() == b"b" * 65539
