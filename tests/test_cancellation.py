import threading

import pytest

from m_dlp import engine


def test_cancel_during_metadata_is_prompt(local_engine):
    cancelled = threading.Event()
    with engine.MagnetDownload(
        "magnet:?xt=urn:btih:" + "a" * 40, cancelled=cancelled.is_set
    ) as download:
        cancelled.set()
        with pytest.raises(engine.DownloadCancelled):
            download.resolve(timeout=30)


def test_cancel_download_saves_state_for_resume(seed, local_engine, tmp_path):
    cancelled = threading.Event()
    target = tmp_path / "downloads"

    def interrupt(progress):
        cancelled.set()

    with (
        pytest.raises(engine.DownloadCancelled),
        engine.MagnetDownload(seed.magnet, cancelled=cancelled.is_set) as download,
    ):
        download.resolve(timeout=10)
        download.download({2}, target, on_progress=interrupt)
    with engine.MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=1)
        assert download.saved_selection(target) == {2}
        download.download({2}, target, stall_timeout=10)
    assert (target / "sample/second.bin").read_bytes() == seed.contents["sample/second.bin"]
