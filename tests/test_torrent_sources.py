import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import libtorrent as lt
import pytest
from test_cli import invoke

from m_dlp.engine import MagnetDownload


@pytest.fixture
def seeded_torrent(seed):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            peers = socket.inet_aton("127.0.0.1") + struct.pack("!H", seed.session.listen_port())
            body = lt.bencode({b"interval": 30, b"peers": peers})
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except ConnectionError:
                # A metadata handle can close while its stopped announce is
                # still in flight; the fixture need not finish that response.
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    data = lt.bdecode(seed.torrent_path.read_bytes())
    data[b"announce"] = f"http://127.0.0.1:{server.server_port}/announce".encode()
    seed.torrent_path.write_bytes(lt.bencode(data))
    try:
        yield seed.torrent_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(
    "seed", [lt.create_torrent.v1_only, lt.create_torrent.v2_only, 0], indirect=True
)
def test_local_torrent_lists_offline_and_downloads_selection(
    seed, seeded_torrent, local_engine, tmp_path
):
    target = tmp_path / "downloads"
    with MagnetDownload(str(seeded_torrent), use_cache=False) as download:
        files = download.resolve(timeout=0.1)
        selected = next(f.number for f in files if f.path.endswith("second.bin"))
        assert not download.metadata_cached
        download.probe(0.1)
        assert download.handle.status().total_payload_download == 0
        download.download({selected}, target, stall_timeout=10)
    assert (target / "sample/second.bin").read_bytes() == seed.contents["sample/second.bin"]
    assert not (target / "sample/first.bin").exists()


def test_torrent_cli_config_download_and_resume_from_magnet(seed, seeded_torrent, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('output = "downloads"\ndownload-limit = "100K"\nupload-limit = "32K"\n')
    result = invoke(str(seeded_torrent), "--largest", "--config", str(config))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "downloads/sample/second.bin").read_bytes() == seed.contents[
        "sample/second.bin"
    ]
    seed.session.pause()
    result = invoke(seed.magnet, "--resume", "--config", str(config), "--download-limit", "0")
    assert result.returncode == 0, result.stderr
    assert "快速恢复" in result.stdout


@pytest.mark.parametrize(
    "seed", [lt.create_torrent.v1_only, lt.create_torrent.v2_only, 0], indirect=True
)
def test_local_torrent_export_preserves_full_metadata(seed, local_engine, tmp_path):
    data = lt.bdecode(seed.torrent_path.read_bytes())
    data[b"announce-list"] = [[b"http://127.0.0.1:1234/announce"]]
    data[b"url-list"] = [b"http://127.0.0.1:1234/files/"]
    seed.torrent_path.write_bytes(lt.bencode(data))
    target = tmp_path / "export.torrent"
    with MagnetDownload(str(seed.torrent_path)) as download:
        download.resolve(timeout=0.1)
        download.export_torrent(target)
        assert download.handle.status().total_payload_download == 0
    assert lt.bdecode(target.read_bytes()) == data


@pytest.mark.parametrize(
    "seed", [lt.create_torrent.v1_only, lt.create_torrent.v2_only, 0], indirect=True
)
def test_magnet_export_roundtrip_without_payload(seed, seeded_torrent, local_engine, tmp_path):
    target = tmp_path / "export.torrent"
    magnet = lt.make_magnet_uri(lt.torrent_info(str(seeded_torrent)))
    with MagnetDownload(magnet, use_cache=False) as download:
        files = download.resolve(timeout=10)
        download.export_torrent(target)
        assert download.handle.status().total_payload_download == 0
    assert (
        lt.torrent_info(str(target)).info_hashes()
        == lt.torrent_info(str(seed.torrent_path)).info_hashes()
    )
    with MagnetDownload(str(target), use_cache=False) as download:
        assert download.resolve(timeout=0.1) == files
        selected = next(f.number for f in files if f.path.endswith("second.bin"))
        download.download({selected}, tmp_path / "downloads", stall_timeout=10)
    assert (tmp_path / "downloads/sample/second.bin").read_bytes() == seed.contents[
        "sample/second.bin"
    ]


def test_export_cli_does_not_download_and_refuses_overwrite(seed, tmp_path):
    exported = tmp_path / "export.torrent"
    output = tmp_path / "downloads"
    result = invoke(seed.magnet, "--export-torrent", str(exported), "-o", str(output))
    assert result.returncode == 0, result.stderr
    assert exported.is_file()
    assert not output.exists()
    exported.write_bytes(b"do not replace")
    result = invoke(str(seed.torrent_path), "--export-torrent", str(exported))
    assert result.returncode != 0
    assert exported.read_bytes() == b"do not replace"


def test_invalid_torrent_cli_reports_clean_errors(tmp_path):
    bad = tmp_path / "broken.torrent"
    bad.write_bytes(b"not a torrent")
    for source in (bad, tmp_path / "missing.torrent", tmp_path):
        result = invoke(str(source), "--list")
        assert result.returncode in (1, 2)
        assert "Traceback" not in result.stderr


def test_limits_apply_to_local_payload_and_changed_resume(seed, local_engine, tmp_path):
    target = tmp_path / "downloads"
    with MagnetDownload(seed.magnet, download_limit=16384, upload_limit=32768) as download:
        download.resolve(timeout=10)
        start = time.monotonic()
        download.download({1, 2}, target, stall_timeout=15)
        assert time.monotonic() - start >= 2
        assert download.handle.download_limit() == 16384
        assert download.handle.upload_limit() == 32768
    with MagnetDownload(seed.magnet, download_limit=0, upload_limit=65536) as download:
        download.resolve(timeout=1)
        download.download({1, 2}, target, stall_timeout=5)
        assert download.resume_loaded
        assert download.handle.download_limit() in (0, -1)
        assert download.handle.upload_limit() == 65536
    for path in ("sample/first.bin", "sample/second.bin"):
        assert (target / path).read_bytes() == seed.contents[path]
