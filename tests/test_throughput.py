import time

import libtorrent as lt
import pytest
from conftest import LOCAL_SETTINGS

from m_dlp.engine import DownloadCancelled, MagnetDownload


def test_unlimited_download_does_not_throttle_tcp_in_a_mixed_swarm(
    local_engine, monkeypatch, tmp_path
):
    # Six real local seeds exercise the TCP peer class, which can impose a
    # second, hidden rate limit even when the session's download limit is zero.
    settings = {
        **LOCAL_SETTINGS,
        "enable_outgoing_utp": True,
        "enable_incoming_utp": True,
        "ignore_limits_on_local_network": False,
    }
    monkeypatch.setattr(
        local_engine, "SESSION_SETTINGS", {**local_engine.SESSION_SETTINGS, **settings}
    )
    source = tmp_path / "seed"
    source.mkdir()
    (source / "payload.bin").write_bytes(b"x" * (8 * 1024**2))
    storage = lt.file_storage()
    storage.add_file("payload.bin", 8 * 1024**2)
    torrent = lt.create_torrent(storage, 32768, lt.create_torrent.v1_only)
    lt.set_piece_hashes(torrent, str(source))
    info = lt.torrent_info(torrent.generate())
    torrent_path = tmp_path / "payload.torrent"
    torrent_path.write_bytes(lt.bencode(torrent.generate()))
    seeds = []
    try:
        for index in range(6):
            tcp = index == 5
            session = lt.session(
                {
                    **settings,
                    "enable_outgoing_tcp": tcp,
                    "enable_incoming_tcp": tcp,
                    "enable_outgoing_utp": not tcp,
                    "enable_incoming_utp": not tcp,
                    "upload_rate_limit": 1024**2 if tcp else 65536,
                }
            )
            params = lt.add_torrent_params()
            params.ti = info
            params.save_path = str(source)
            params.flags &= ~(lt.torrent_flags.paused | lt.torrent_flags.auto_managed)
            params.flags |= lt.torrent_flags.seed_mode | lt.torrent_flags.disable_pex
            seeds.append((session, session.add_torrent(params)))

        with MagnetDownload(str(torrent_path), use_cache=False) as download:
            download.resolve(timeout=5)
            policy = download.session.get_settings()["mixed_mode_algorithm"]
            # First establish that this swarm actually triggers the native
            # throttle. Then restore the application's policy and verify its
            # effect, rather than merely asserting a settings constant.
            download.session.apply_settings({"mixed_mode_algorithm": 1})
            deadline = time.monotonic() + 20
            connected = False
            restored_at = None

            def inspect(_progress):
                nonlocal connected, restored_at
                now = time.monotonic()
                assert now < deadline, "Mixed TCP/uTP swarm did not become active"
                if not connected:
                    endpoint = ("127.0.0.1", download.session.listen_port())
                    for _session, handle in seeds:
                        handle.connect_peer(endpoint)
                    connected = True
                peers = download.handle.get_peer_info()
                # peer_info::utp_socket is not exposed by the Python binding.
                tcp = [peer for peer in peers if not peer.flags & (1 << 17)]
                utp = [peer for peer in peers if peer.flags & (1 << 17)]
                active = (
                    len(tcp) == 1
                    and len(utp) == 5
                    and all(peer.payload_down_speed > 0 for peer in peers)
                )
                limit = download.session.get_peer_class(lt.session.tcp_peer_class_id)[
                    "download_limit"
                ]
                if restored_at is None:
                    if active and limit > 0:
                        download.session.apply_settings({"mixed_mode_algorithm": policy})
                        restored_at = now
                elif active and now - restored_at >= 2:
                    assert download.session.get_settings()["download_rate_limit"] == 0
                    assert limit == 0, f"Unlimited downloads still throttle TCP to {limit} B/s"
                    raise DownloadCancelled("Mixed transport policy verified")

            with pytest.raises(DownloadCancelled):
                download.download({1}, tmp_path / "download", on_progress=inspect)
    finally:
        for session, _handle in seeds:
            session.pause()
