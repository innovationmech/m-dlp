"""Network observations and byte-accurate file availability."""

from dataclasses import dataclass
from urllib.parse import urlsplit

import libtorrent as lt


def tracker_label(url: str) -> str:
    """Do not print tracker credentials, passkey paths, or query strings."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or "unknown"
        if ":" in host:
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}"
    except ValueError:
        return "无效 tracker 地址"


@dataclass(frozen=True)
class FileAvailability:
    number: int
    path: str
    size: int
    observed_bytes: int
    local_bytes: int
    missing_pieces: int
    total_pieces: int

    @property
    def percent(self) -> float:
        return self.observed_bytes / self.size * 100 if self.size else 100.0


def file_coverage(
    number: int,
    path: str,
    size: int,
    offset: int,
    piece_length: int,
    availability: list[int],
    have_pieces: list[bool],
) -> FileAvailability:
    observed = local = missing = count = 0
    end = offset + size
    if size:
        for piece in range(offset // piece_length, (end - 1) // piece_length + 1):
            count += 1
            overlap = min(end, (piece + 1) * piece_length) - max(offset, piece * piece_length)
            have = piece < len(have_pieces) and have_pieces[piece]
            remote = piece < len(availability) and availability[piece] > 0
            if have:
                local += overlap
            if have or remote:
                observed += overlap
            else:
                missing += 1
    return FileAvailability(number, path, size, observed, local, missing, count)


@dataclass(frozen=True)
class TrackerStatus:
    label: str
    state: str
    detail: str = ""


@dataclass(frozen=True)
class DiagnosticSnapshot:
    peers: int
    seeds: int
    dht_enabled: bool
    dht_nodes: int | None
    trackers: tuple[TrackerStatus, ...]
    files: tuple[FileAvailability, ...]
    network_errors: tuple[str, ...]


class NetworkObservations:
    def __init__(self, trackers):
        self.trackers = {url: TrackerStatus(tracker_label(url), "等待响应") for url in trackers}
        self.dht_nodes = None
        self.network_errors: list[str] = []

    def observe(self, alert) -> None:
        if isinstance(alert, lt.tracker_error_alert):
            self.trackers[alert.tracker_url()] = TrackerStatus(
                tracker_label(alert.tracker_url()), "失败", alert.error.message()
            )
        elif isinstance(alert, lt.tracker_reply_alert):
            self.trackers[alert.tracker_url()] = TrackerStatus(
                tracker_label(alert.tracker_url()), "已响应", f"返回 {alert.num_peers} 个节点"
            )
        elif isinstance(alert, lt.tracker_announce_alert):
            # Retain the last actual result while a retry is in flight.
            self.trackers.setdefault(
                alert.tracker_url(), TrackerStatus(tracker_label(alert.tracker_url()), "请求中")
            )
        elif isinstance(alert, lt.dht_stats_alert):
            self.dht_nodes = sum(bucket["num_nodes"] for bucket in alert.routing_table)
        elif isinstance(alert, lt.listen_failed_alert):
            message = f"监听失败：{alert.error.message()}"
            if message not in self.network_errors:
                self.network_errors = [*self.network_errors[-4:], message]
