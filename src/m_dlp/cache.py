"""Private, atomic metadata and fast-resume cache with safe fallbacks."""

import base64
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

import libtorrent as lt


def default_cache_dir() -> Path:
    if override := os.environ.get("M_DLP_CACHE_DIR"):
        return Path(override).expanduser()
    if xdg := os.environ.get("XDG_CACHE_HOME"):
        return Path(xdg) / "m-dlp"
    if sys.platform == "darwin":
        return Path.home() / "Library/Caches/m-dlp"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "m-dlp"
    return Path.home() / ".cache/m-dlp"


def hash_keys(hashes) -> list[str]:
    keys = []
    if hashes.has_v2():
        keys.append(str(hashes.v2))
    if hashes.has_v1():
        keys.append(str(hashes.v1))
    return keys


def matches_hashes(actual, expected) -> bool:
    return set(hash_keys(expected)) <= set(hash_keys(actual))


def _manifest(info, destination: Path) -> dict:
    storage = info.files()
    paths = [
        destination / storage.file_path(i)
        for i in range(storage.num_files())
        if not storage.file_flags(i)
        & (lt.file_storage.flag_pad_file | lt.file_storage.flag_symlink)
    ]
    paths.extend(destination / f".{key}.parts" for key in hash_keys(info.info_hashes()))
    manifest = {}
    for path in paths:
        try:
            stat = path.stat(follow_symlinks=False)
            manifest[str(path.relative_to(destination))] = [
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
                stat.st_ino,
                stat.st_dev,
                stat.st_mode,
            ]
        except FileNotFoundError:
            manifest[str(path.relative_to(destination))] = None
    return manifest


@dataclass
class ResumeState:
    selected: set[int]
    params: object | None


class CacheStore:
    def __init__(self, root: Path | None = None, *, enabled: bool = True):
        self.root = (root or default_cache_dir()).expanduser()
        self.enabled = enabled
        self.warnings: list[str] = []

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def _write(self, path: Path, data: bytes) -> None:
        temporary = None
        try:
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            # NamedTemporaryFile creates mode 0600 files on POSIX.
            temporary.replace(path)
        except OSError as exc:
            self.warn(f"无法保存缓存，将继续下载：{exc}")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load_metadata(self, hashes):
        if not self.enabled:
            return None
        for key in hash_keys(hashes):
            try:
                data = (self.root / f"{key}.torrent").read_bytes()
                decoded = lt.bdecode(data)
                # The info hash does not cover top-level tracker/web-seed
                # fields. Strip those on read, even from a valid cache file.
                info = lt.torrent_info({b"info": decoded[b"info"]})
                if not matches_hashes(info.info_hashes(), hashes):
                    raise ValueError("缓存的 info hash 不匹配")
                return info
            except FileNotFoundError:
                continue
            except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
                self.warn(f"元数据缓存不可用，将重新获取：{exc}")
        return None

    def save_metadata(self, info) -> None:
        if self.enabled:
            # Only cache the info dictionary. Network sources come from the
            # current magnet, never from an old or edited cache file.
            data = lt.bencode({b"info": lt.bdecode(info.info_section())})
            for key in hash_keys(info.info_hashes()):
                self._write(self.root / f"{key}.torrent", data)

    def _state_path(self, hashes, destination: Path) -> Path:
        destination_id = hashlib.sha256(str(destination).encode()).hexdigest()
        return self.root / "resume" / f"{hash_keys(hashes)[0]}-{destination_id}.json"

    def save_state(self, params, info, destination: Path, selected: set[int]) -> None:
        if not self.enabled:
            return
        try:
            state = {
                "version": 1,
                "destination": str(destination),
                "selected": sorted(selected),
                "files": _manifest(info, destination),
                "resume": base64.b64encode(lt.write_resume_data_buf(params)).decode("ascii"),
            }
            self._write(
                self._state_path(info.info_hashes(), destination), json.dumps(state).encode()
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self.warn(f"无法保存恢复状态，将在下次启动时重新校验：{exc}")

    def load_state(self, info, destination: Path) -> ResumeState | None:
        if not self.enabled:
            return None
        try:
            state = json.loads(self._state_path(info.info_hashes(), destination).read_bytes())
            if state["version"] != 1 or state["destination"] != str(destination):
                raise ValueError("恢复状态版本或输出目录不匹配")
            selected = state["selected"]
            storage = info.files()
            valid = {
                i + 1
                for i in range(storage.num_files())
                if not storage.file_flags(i)
                & (lt.file_storage.flag_pad_file | lt.file_storage.flag_symlink)
            }
            if (
                not isinstance(selected, list)
                or not selected
                or any(type(n) is not int for n in selected)
                or not set(selected) <= valid
            ):
                raise ValueError("恢复状态中的文件选择无效")
            params = lt.read_resume_data(base64.b64decode(state["resume"], validate=True))
            if not matches_hashes(params.info_hashes, info.info_hashes()):
                raise ValueError("恢复状态的 info hash 不匹配")
            if params.ti is not None and not matches_hashes(
                params.ti.info_hashes(), info.info_hashes()
            ):
                raise ValueError("恢复状态的元数据不匹配")
            if state["files"] != _manifest(info, destination):
                self.warn("已有文件发生变化，已保留选择记录并回退到完整校验。")
                params = None
            return ResumeState(set(selected), params)
        except FileNotFoundError:
            return None
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            self.warn(f"恢复状态不可用，将重新校验：{exc}")
            return None
