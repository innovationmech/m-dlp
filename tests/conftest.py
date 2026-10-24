import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import libtorrent as lt
import pytest

# Keep test swarms on localhost, with no public trackers or DHT traffic.
LOCAL_SETTINGS = {
    "listen_interfaces": "127.0.0.1:0",
    "enable_dht": False,
    "enable_lsd": False,
    "enable_upnp": False,
    "enable_natpmp": False,
    "enable_outgoing_utp": False,
    "enable_incoming_utp": False,
    "allow_multiple_connections_per_ip": True,
    "close_redundant_connections": False,
}


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("M_DLP_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.setenv("M_DLP_QUEUE_DB", str(tmp_path / "state/queue.sqlite3"))
    monkeypatch.delenv("M_DLP_CONFIG", raising=False)


@dataclass
class Seed:
    magnet: str
    contents: dict[str, bytes]
    session: object
    torrent_path: Path


@pytest.fixture
def seed(tmp_path, request):
    root = tmp_path / "seed"
    contents = {
        "sample/first.bin": b"a" * 32771,
        "sample/second.bin": b"b" * 65539,
        "sample/empty.txt": b"",
    }
    storage = lt.file_storage()
    for name, data in contents.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        storage.add_file(name, len(data))
    torrent = lt.create_torrent(
        storage, 16384, getattr(request, "param", lt.create_torrent.v1_only)
    )
    lt.set_piece_hashes(torrent, str(root))
    info = lt.torrent_info(torrent.generate())
    torrent_path = tmp_path / "sample.torrent"
    torrent_path.write_bytes(lt.bencode(torrent.generate()))
    session = lt.session(LOCAL_SETTINGS)
    params = lt.add_torrent_params()
    params.ti = info
    params.save_path = str(root)
    params.flags &= ~(lt.torrent_flags.paused | lt.torrent_flags.auto_managed)
    handle = session.add_torrent(params)
    deadline = time.monotonic() + 10
    while not handle.status().is_seeding:
        assert time.monotonic() < deadline, "Local seeder did not start"
        time.sleep(0.02)
    magnet = lt.make_magnet_uri(info) + f"&x.pe=127.0.0.1:{session.listen_port()}"
    yield Seed(magnet, contents, session, torrent_path)
    session.pause()
    session.remove_torrent(handle)


@pytest.fixture
def local_engine(monkeypatch):
    from m_dlp import engine

    monkeypatch.setattr(engine, "SESSION_SETTINGS", LOCAL_SETTINGS)
    return engine


@pytest.fixture
def tracker(request):
    failure = getattr(request, "param", False)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = lt.bencode(
                {b"failure reason": b"test tracker unavailable"}
                if failure
                else {b"interval": 3600, b"peers": b""}
            )
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/secret/announce?passkey=hidden"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
