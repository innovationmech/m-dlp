"""BitTorrent operations, independent of terminal presentation."""

import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

import libtorrent as lt

from .cache import CacheStore
from .diagnostics import DiagnosticSnapshot, NetworkObservations, file_coverage
from .limits import validate_rate
from .source import TorrentSource

SESSION_SETTINGS = {
    "listen_interfaces": "0.0.0.0:0,[::]:0",
    "user_agent": "m-dlp/0.1.0",
}
CHECKPOINT_INTERVAL = 30


class DownloadError(RuntimeError):
    """An actionable torrent or filesystem failure."""


class MetadataTimeout(DownloadError):
    pass


class DownloadStalled(DownloadError):
    pass


class DownloadCancelled(Exception):
    """Cooperative cancellation; final resume checkpoints still run."""


@dataclass(frozen=True)
class TorrentFile:
    number: int
    path: str
    size: int


@dataclass(frozen=True)
class DownloadProgress:
    completed: int
    total: int
    rate: int
    peers: int
    state: str


def _validate_timeout(value: float, *, allow_zero: bool = False) -> None:
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        raise ValueError("超时必须是有限的正数（停滞超时可设为 0）。")


class MagnetDownload:
    """Own one session and torrent; close it on success, failure, or Ctrl+C.

    Metadata is fetched into a temporary directory with payload requests
    disabled. Only after selection is storage moved to the destination.
    """

    def __init__(
        self,
        source: str,
        *,
        cache_dir: Path | None = None,
        use_cache: bool = True,
        download_limit: int = 0,
        upload_limit: int = 0,
        cancelled: Callable[[], bool] | None = None,
    ):
        self._cancelled = cancelled
        self.download_limit = validate_rate(download_limit)
        self.upload_limit = validate_rate(upload_limit)
        self._source = TorrentSource(source)
        self._params = self._source.parameters()
        self.cache = CacheStore(cache_dir, enabled=use_cache)
        self.warnings = self.cache.warnings
        self.metadata_cached = False
        self.resume_loaded = False
        self._destination: Path | None = None
        self._selected: set[int] = set()
        self._save_pending = False
        self._last_checkpoint = 0.0
        self._observations = NetworkObservations(self._params.trackers)
        self._last_dht_request = 0.0
        self.session = None
        self.handle = None
        self._temporary = None
        self._files: tuple[TorrentFile, ...] = ()
        self._info = None
        self.name = ""

    def __enter__(self):
        self._check_cancelled()
        self._temporary = TemporaryDirectory(prefix="m-dlp-")
        try:
            categories = lt.alert.category_t
            self.session = lt.session(
                {
                    **SESSION_SETTINGS,
                    "download_rate_limit": self.download_limit,
                    "upload_rate_limit": self.upload_limit,
                    "ignore_limits_on_local_network": False,
                    "close_redundant_connections": False,
                    "alert_mask": int(
                        categories.error_notification
                        | categories.storage_notification
                        | categories.status_notification
                        | categories.tracker_notification
                    ),
                }
            )
            params = self._params
            cached = self.cache.load_metadata(params.info_hashes) if params.ti is None else None
            if cached is not None:
                params.ti = cached
                self.metadata_cached = True
            params.save_path = self._temporary.name
            params.download_limit = self.download_limit or -1
            params.upload_limit = self.upload_limit or -1
            # A magnet may include so= (file priorities). Explicit CLI selection
            # is authoritative; no payload may arrive before that selection.
            params.file_priorities = []
            params.flags |= lt.torrent_flags.upload_mode | lt.torrent_flags.default_dont_download
            params.flags &= ~(lt.torrent_flags.auto_managed | lt.torrent_flags.paused)
            self.handle = self.session.add_torrent(params)
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def _check_cancelled(self) -> None:
        if self._cancelled is not None and self._cancelled():
            raise DownloadCancelled("任务已取消，已下载的数据会保留。")

    def close(self) -> None:
        if self.session is not None:
            self.session.pause()
            if self.handle is not None and self.handle.is_valid():
                try:
                    self._checkpoint()
                except (OSError, RuntimeError) as exc:
                    self.cache.warn(f"保存恢复状态失败，下次启动会重新校验：{exc}")
                # No delete_files flag: downloaded/partial data is preserved.
                self.session.remove_torrent(self.handle)
            self.handle = None
            # Destroying the session joins its worker threads before cleaning
            # temporary storage, including on KeyboardInterrupt.
            self.session = None
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None

    def _request_checkpoint(self) -> None:
        if self.cache.enabled and self._destination is not None and not self._save_pending:
            self.handle.save_resume_data(
                lt.torrent_handle.flush_disk_cache | lt.torrent_handle.save_info_dict
            )
            self._save_pending = True
            self._last_checkpoint = time.monotonic()

    def _checkpoint(self) -> None:
        if not self.cache.enabled or self._destination is None:
            return
        # Finish an older periodic checkpoint before requesting the final one.
        for final in (False, True):
            if final:
                self._request_checkpoint()
            deadline = time.monotonic() + 5
            while self._save_pending:
                self._alerts(check_errors=False)
                if time.monotonic() >= deadline:
                    raise DownloadError("保存恢复状态超时")
                self.session.wait_for_alert(100)

    def _alerts(self, *, check_errors: bool = True, check_status: bool = True) -> list:
        if check_errors:
            self._check_cancelled()
        alerts = self.session.pop_alerts()
        for alert in alerts:
            self._observations.observe(alert)
            if getattr(alert, "handle", self.handle) != self.handle:
                continue
            if isinstance(alert, lt.save_resume_data_alert):
                self._save_pending = False
                self.cache.save_state(alert.params, self._info, self._destination, self._selected)
            elif isinstance(alert, lt.save_resume_data_failed_alert):
                self._save_pending = False
                self.cache.warn(f"保存恢复状态失败：{alert.error.message()}")
            elif isinstance(alert, lt.fastresume_rejected_alert):
                self.resume_loaded = False
                self.cache.warn("libtorrent 拒绝恢复快照，已回退到文件校验。")
            if check_errors and isinstance(
                alert, (lt.torrent_error_alert, lt.file_error_alert, lt.storage_moved_failed_alert)
            ):
                raise DownloadError(alert.message())
        if check_errors and check_status and self.handle is not None and self.handle.is_valid():
            status = self.handle.status()
            if status.errc.value():
                raise DownloadError(status.errc.message())
        return alerts

    def diagnostics(self, selected: set[int] | None = None) -> DiagnosticSnapshot:
        status = self.handle.status(lt.status_flags_t.query_pieces)
        available = self.handle.piece_availability() if status.has_metadata else []
        files = []
        if self._info is not None:
            storage = self._info.files()
            files = [
                file_coverage(
                    file.number,
                    file.path,
                    file.size,
                    storage.file_offset(file.number - 1),
                    self._info.piece_length(),
                    available,
                    status.pieces,
                )
                for file in self._files
                if selected is None or file.number in selected
            ]
        enabled = self.session.is_dht_running()
        return DiagnosticSnapshot(
            status.num_peers,
            status.num_seeds,
            enabled,
            self._observations.dht_nodes if enabled else 0,
            tuple(self._observations.trackers.values()),
            tuple(files),
            tuple(self._observations.network_errors),
        )

    def _request_dht_stats(self) -> None:
        if time.monotonic() - self._last_dht_request >= 1:
            self.session.post_dht_stats()
            self._last_dht_request = time.monotonic()

    def probe(self, seconds: float = 10, selected: set[int] | None = None) -> DiagnosticSnapshot:
        _validate_timeout(seconds)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._request_dht_stats()
            self._alerts()
            self.session.wait_for_alert(100)
        self._alerts()
        return self.diagnostics(selected)

    def _wait_for(self, alert_type: type, description: str, timeout: float = 30) -> None:
        deadline = time.monotonic() + timeout
        while True:
            if any(
                isinstance(alert, alert_type)
                and getattr(alert, "handle", self.handle) == self.handle
                # Once removal is requested, is_valid() followed by status()
                # races the native thread. Observe removal alerts without
                # querying that handle; cancellation/error alerts still apply.
                for alert in self._alerts(check_status=alert_type is not lt.torrent_removed_alert)
            ):
                return
            if time.monotonic() >= deadline:
                raise DownloadError(f"{description}超时。")
            self.session.wait_for_alert(100)

    def resolve(
        self, timeout: float = 120, on_status: Callable[[int], None] | None = None
    ) -> tuple[TorrentFile, ...]:
        _validate_timeout(timeout)
        deadline = time.monotonic() + timeout
        while True:
            self._request_dht_stats()
            self._alerts()
            status = self.handle.status()
            if status.has_metadata:
                break
            if on_status is not None:
                on_status(status.num_peers)
            if time.monotonic() >= deadline:
                raise MetadataTimeout(
                    f"{timeout:g} 秒内未获取到文件列表。可能没有在线节点，"
                    "或 tracker / DHT 不可达；可增大 --metadata-timeout 后重试。"
                )
            self.session.wait_for_alert(100)
        self._info = self.handle.torrent_file()
        self.name = self._info.name()
        storage = self._info.files()
        self._files = tuple(
            TorrentFile(index + 1, storage.file_path(index), storage.file_size(index))
            for index in range(storage.num_files())
            if not storage.file_flags(index)
            & (lt.file_storage.flag_pad_file | lt.file_storage.flag_symlink)
        )
        if not self._files:
            raise DownloadError("该资源不包含可选择的普通文件。")
        if not self.metadata_cached:
            self.cache.save_metadata(self._info)
        return self._files

    def export_torrent(self, destination: Path) -> None:
        if self._info is None:
            raise ValueError("请先读取资源元数据。")
        data = self._source.data
        if data is None:
            params = self._source.parameters()
            params.ti = self._info
            flags = lt.write_flags.include_dht_nodes
            try:
                data = lt.bencode(lt.write_torrent_file(params, flags))
            except RuntimeError:
                # BEP 9 metadata does not include BEP 52 piece layers. This is
                # libtorrent's supported metadata-only export format; peers can
                # supply the missing hashes when the file is used to download.
                data = lt.bencode(
                    lt.write_torrent_file(params, flags | lt.write_flags.allow_missing_piece_layer)
                )
                self.cache.warn(
                    "导出的 v2 种子尚缺 piece layers，m-dlp 可从在线节点补齐；"
                    "部分其他客户端可能不支持。"
                )
        destination = destination.expanduser().absolute()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(
                dir=destination.parent, prefix=".m-dlp-", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # Atomic publication without overwriting existing files or symlinks.
            os.link(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def saved_selection(self, destination: Path) -> set[int] | None:
        state = self.cache.load_state(self._info, destination.expanduser().resolve())
        return state.selected if state else None

    def download(
        self,
        selected: set[int],
        destination: Path,
        stall_timeout: float = 300,
        on_progress: Callable[[DownloadProgress], None] | None = None,
        *,
        recheck: bool = False,
    ) -> None:
        _validate_timeout(stall_timeout, allow_zero=True)
        self._check_cancelled()
        available = {file.number for file in self._files}
        if not selected or not selected <= available:
            raise ValueError("请选择文件列表中至少一个有效编号。")
        destination = destination.expanduser().resolve()
        # Existing symlinks must not redirect torrent writes outside the chosen
        # folder. Check all ordinary paths, since a piece can span files.
        for file in self._files:
            path = destination / file.path
            if not path.resolve().is_relative_to(destination):
                raise DownloadError(f"文件路径超出输出目录：{file.path}")
            if any(part.is_symlink() for part in (path, *path.parents) if part != destination):
                raise DownloadError(f"输出路径含有符号链接：{file.path}")
        destination.mkdir(parents=True, exist_ok=True)

        state = None if recheck else self.cache.load_state(self._info, destination)
        restored = state.params if state else None
        fresh = self._source.parameters()
        params = restored if restored is not None else fresh
        # Restore verified pieces only. The current request owns every path,
        # source and priority; resume files cannot redirect writes or downloads.
        params.ti = self._info
        params.save_path = str(destination)
        params.download_limit = self.download_limit or -1
        params.upload_limit = self.upload_limit or -1
        params.renamed_files = {}
        for name in ("trackers", "tracker_tiers", "url_seeds", "http_seeds", "peers", "dht_nodes"):
            setattr(params, name, getattr(fresh, name))
        params.piece_priorities = []
        priorities = [4 if index + 1 in selected else 0 for index in range(self._info.num_files())]
        params.file_priorities = priorities
        params.flags = (lt.add_torrent_params().flags | lt.torrent_flags.default_dont_download) & ~(
            lt.torrent_flags.paused | lt.torrent_flags.auto_managed
        )
        self.session.remove_torrent(self.handle)
        self._wait_for(lt.torrent_removed_alert, "切换下载任务")
        self.handle = self.session.add_torrent(params)
        self.session.apply_settings(
            {
                "close_redundant_connections": SESSION_SETTINGS.get(
                    "close_redundant_connections", True
                )
            }
        )
        self.resume_loaded = restored is not None
        self._destination = destination
        self._selected = set(selected)
        self._last_checkpoint = time.monotonic()

        chosen = [file for file in self._files if file.number in selected]
        total = sum(file.size for file in chosen)
        last_activity = time.monotonic()
        previous_payload = -1
        checking_states = {lt.torrent_status.checking_files, lt.torrent_status.checking_resume_data}
        while True:
            self._request_dht_stats()
            self._alerts()
            if time.monotonic() - self._last_checkpoint >= CHECKPOINT_INTERVAL:
                self._request_checkpoint()
            status = self.handle.status()
            # Only hash-verified pieces count toward completion, including
            # torrents whose selected file shares pieces with skipped files.
            progress = self.handle.file_progress(lt.torrent_handle.piece_granularity)
            completed = sum(min(file.size, progress[file.number - 1]) for file in chosen)
            checking = status.state in checking_states
            if on_progress is not None:
                on_progress(
                    DownloadProgress(
                        completed,
                        total,
                        status.download_payload_rate,
                        status.num_peers,
                        "校验已有文件" if checking else "下载中",
                    )
                )
            if not checking and status.is_finished and completed == total:
                self.handle.pause()
                self.handle.flush_cache()
                self._wait_for(lt.cache_flushed_alert, "写入文件")
                return
            if checking or status.total_payload_download != previous_payload:
                previous_payload = status.total_payload_download
                last_activity = time.monotonic()
            if stall_timeout and time.monotonic() - last_activity >= stall_timeout:
                raise DownloadStalled(
                    f"连续 {stall_timeout:g} 秒未收到文件数据（已连接 {status.num_peers} 个节点）。"
                    "已保留下载进度，可稍后使用相同输出目录重试，或调整 --stall-timeout。"
                )
            self.session.wait_for_alert(200)
