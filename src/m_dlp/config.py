"""Read opt-in CLI defaults from a strictly validated TOML file."""

import argparse
import os
import sys
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path


def default_config_path() -> Path:
    if root := os.environ.get("XDG_CONFIG_HOME"):
        return Path(root) / "m-dlp/config.toml"
    if sys.platform == "win32" and (root := os.environ.get("APPDATA")):
        return Path(root) / "m-dlp/config.toml"
    return Path.home() / ".config/m-dlp/config.toml"


def load_defaults(
    path: Path | None,
    *,
    disabled: bool,
    converters: Mapping[str, Callable],
) -> dict:
    if disabled:
        return {}
    explicit = path is not None or bool(os.environ.get("M_DLP_CONFIG"))
    try:
        path = path or Path(os.environ.get("M_DLP_CONFIG") or default_config_path())
        path = path.expanduser().absolute()
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if not explicit:
                return {}
            raise
        result = {}
        for key, value in data.items():
            if key not in converters:
                raise ValueError(f"不支持的配置项：{key}")
            if key == "no-cache":
                if type(value) is not bool:
                    raise ValueError("no-cache 必须是 true 或 false")
                converted = value
            elif key == "network-interface":
                if not isinstance(value, str):
                    raise ValueError("network-interface 必须是非空接口名称字符串")
                try:
                    converted = converters[key](value)
                except (ValueError, argparse.ArgumentTypeError) as exc:
                    raise ValueError(f"{key}：{exc}") from exc
            elif key in {"output", "cache-dir", "queue-db"}:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{key} 必须是非空路径字符串")
                if "\x00" in value:
                    raise ValueError(f"{key} 路径不能包含 NUL 字符")
                converted = Path(value).expanduser()
                if not converted.is_absolute():
                    converted = path.parent / converted
            else:
                if type(value) not in (str, int, float):
                    raise ValueError(f"{key} 必须是数字或字符串")
                try:
                    converted = converters[key](str(value))
                except (ValueError, argparse.ArgumentTypeError) as exc:
                    raise ValueError(f"{key}：{exc}") from exc
            result[key.replace("-", "_")] = converted
        return result
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        raise ValueError(f"配置文件 {path or '默认路径'}：{exc}") from exc
