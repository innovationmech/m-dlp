"""Read an immutable magnet or local torrent source, retaining network metadata."""

from pathlib import Path

import libtorrent as lt


class TorrentSource:
    def __init__(self, value: str):
        self.magnet = None
        self.data = None
        if value.startswith("magnet:?"):
            self.magnet = value
        else:
            if not value or "://" in value or value.startswith("magnet:"):
                raise ValueError("请输入 magnet:? 磁力链接或本地 .torrent 文件路径。")
            self.data = Path(value).expanduser().read_bytes()

    def parameters(self):
        try:
            if self.magnet is not None:
                return lt.parse_magnet_uri(self.magnet)
            return lt.load_torrent_buffer(self.data)
        except RuntimeError as exc:
            if self.magnet is not None:
                raise ValueError("磁力链接缺少有效的 BitTorrent info hash。") from exc
            raise ValueError(f"无效的 .torrent 文件：{exc}") from exc
