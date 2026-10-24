"""Bandwidth limits in bytes per second, bounded by libtorrent's signed int API."""

from .selection import parse_size

MAX_RATE = 2**31 - 1


def validate_rate(value: int) -> int:
    if type(value) is not int or not 0 <= value <= MAX_RATE:
        raise ValueError(f"限速须为 0 到 {MAX_RATE} 字节/秒，0 表示不限速。")
    return value


def parse_rate(value: str) -> int:
    return validate_rate(parse_size(value))
