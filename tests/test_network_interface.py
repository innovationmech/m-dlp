import socket
import time
from threading import Event

import pytest

from m_dlp import engine

MAGNET = "magnet:?xt=urn:btih:" + "a" * 40


def loopback_interface():
    names = {name for _index, name in socket.if_nameindex()}
    for name in ("lo0", "lo"):
        if name in names:
            return name
    pytest.skip("No named loopback interface available")


@pytest.mark.parametrize("name", ["", "utun8,lo0", "utun8:123", "[::1]", "utun8\x00", "utun8\n"])
def test_interface_name_cannot_inject_libtorrent_settings(name):
    with pytest.raises(ValueError):
        engine.MagnetDownload(MAGNET, network_interface=name)


def test_missing_interface_fails_before_session_creation(monkeypatch):
    def unexpected_session(*args, **kwargs):
        pytest.fail("An invalid interface must not start a network session")

    monkeypatch.setattr(engine.lt, "session", unexpected_session)
    with pytest.raises(engine.DownloadError, match="网络接口"):
        with engine.MagnetDownload(MAGNET, network_interface="m_dlp_missing_iface_83923"):
            pass


def test_default_keeps_existing_interface_settings(local_engine):
    with engine.MagnetDownload(MAGNET) as download:
        settings = download.session.get_settings()
        assert settings["listen_interfaces"] == engine.SESSION_SETTINGS["listen_interfaces"]
        assert settings["outgoing_interfaces"] == ""


def test_named_interface_binds_tcp_and_udp_and_downloads(seed, local_engine, tmp_path):
    interface = loopback_interface()
    with engine.MagnetDownload(seed.magnet, network_interface=interface) as download:
        settings = download.session.get_settings()
        assert settings["outgoing_interfaces"] == interface
        assert settings["listen_interfaces"] == f"{interface}:0"
        download.resolve(timeout=10)
        peers = download.handle.get_peer_info()
        assert peers
        assert all(peer.local_endpoint[0] in ("127.0.0.1", "::1") for peer in peers)
        download.download({2}, tmp_path / "downloads", stall_timeout=10)
    assert (tmp_path / "downloads/sample/second.bin").read_bytes() == b"b" * 65539


@pytest.mark.parametrize("replaced", [False, True])
def test_interface_loss_stops_session_and_preserves_resume(
    seed, local_engine, tmp_path, monkeypatch, replaced
):
    interface = loopback_interface()
    index = socket.if_nametoindex(interface)
    target = tmp_path / "downloads"

    def unavailable(name):
        assert name == interface
        if replaced:
            return index + 1000
        raise OSError("interface disappeared")

    def drop_interface(_progress):
        monkeypatch.setattr(engine.socket, "if_nametoindex", unavailable)

    with engine.MagnetDownload(seed.magnet, network_interface=interface) as download:
        download.resolve(timeout=10)
        with pytest.raises(engine.DownloadError, match="网络接口"):
            download.download({2}, target, on_progress=drop_interface)
        assert download.session.is_paused()

    with engine.MagnetDownload(seed.magnet) as restored:
        restored.resolve(timeout=10)
        assert restored.saved_selection(target) == {2}
        restored.download({2}, target, stall_timeout=10)
    assert (target / "sample/second.bin").read_bytes() == seed.contents["sample/second.bin"]


def test_batch_preserves_interface_error_during_torrent_handoff(
    seed, local_engine, tmp_path, monkeypatch
):
    from m_dlp import cli
    from m_dlp.batch import BatchTask

    interface = loopback_interface()
    args = cli._parse_args([str(seed.torrent_path), "--all", "--network-interface", interface])
    task = BatchTask(1, 1, str(seed.torrent_path), "local-test", tmp_path / "downloads")
    wait_for = engine.MagnetDownload._wait_for

    def unavailable(_name):
        raise OSError("interface disappeared")

    def lose_interface_after_removal(download, alert_type, *args, **kwargs):
        if alert_type is engine.lt.torrent_removed_alert:
            # Reproduce loss after the native metadata handle is invalidated,
            # before its removal alert permits adding the payload torrent.
            deadline = time.monotonic() + 3
            while download.handle.is_valid():
                assert time.monotonic() < deadline
                time.sleep(0.01)
            monkeypatch.setattr(engine.socket, "if_nametoindex", unavailable)
        return wait_for(download, alert_type, *args, **kwargs)

    monkeypatch.setattr(engine.MagnetDownload, "_wait_for", lose_interface_after_removal)
    result = cli._batch_worker(task, Event(), lambda event: None, args=args)
    assert result.state == "failed"
    assert "网络接口" in result.error
    assert interface in result.error
    assert result.diagnostics is None
