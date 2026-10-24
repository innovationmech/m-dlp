from urllib.parse import quote

import pytest

from m_dlp.diagnostics import file_coverage, tracker_label
from m_dlp.engine import MagnetDownload


def test_coverage_counts_byte_overlap_and_local_verified_pieces():
    coverage = file_coverage(1, "part.bin", 20, 10, 16, [1, 0], [False, False])
    assert coverage.observed_bytes == 6
    assert coverage.missing_pieces == 1
    assert coverage.total_pieces == 2
    coverage = file_coverage(1, "part.bin", 20, 10, 16, [1, 0], [False, True])
    assert coverage.observed_bytes == 20
    assert coverage.local_bytes == 14
    assert coverage.missing_pieces == 0


def test_empty_file_requires_no_pieces():
    coverage = file_coverage(1, "empty", 0, 0, 16, [], [])
    assert coverage.total_pieces == coverage.missing_pieces == 0
    assert coverage.percent == 100


def test_tracker_labels_do_not_expose_passkeys():
    assert (
        tracker_label("https://user:secret@tracker.example:443/passkey/announce?token=abc")
        == "https://tracker.example:443"
    )


def test_probe_observes_seed_without_downloading_payload(seed, local_engine):
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
        snapshot = download.probe(1, selected={2})
        assert snapshot.dht_enabled is False
        assert len(snapshot.files) == 1
        assert snapshot.files[0].number == 2
        assert snapshot.files[0].percent == 100
        assert snapshot.files[0].local_bytes == 0
        assert download.handle.status().total_payload_download == 0


def test_offline_diagnostics_reports_unobserved_pieces(seed, local_engine):
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=10)
    seed.session.pause()
    with MagnetDownload(seed.magnet) as download:
        download.resolve(timeout=1)
        snapshot = download.probe(0.2, selected={2})
        assert snapshot.peers == 0
        assert snapshot.files[0].percent == 0
        assert snapshot.files[0].missing_pieces > 0


@pytest.mark.parametrize("tracker", [False, True], indirect=True)
def test_real_tracker_responses_and_errors_are_reported(seed, tracker, local_engine):
    with MagnetDownload(seed.magnet + "&tr=" + quote(tracker, safe="")) as download:
        download.resolve(timeout=10)
        snapshot = download.probe(0.5)
        assert len(snapshot.trackers) == 1
        assert snapshot.trackers[0].state in {"已响应", "失败"}
        assert "secret" not in snapshot.trackers[0].label
        assert "passkey" not in snapshot.trackers[0].label
        assert download.handle.status().total_payload_download == 0
