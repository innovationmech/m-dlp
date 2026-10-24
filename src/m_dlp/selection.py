"""Parse the one-based file numbers displayed by the CLI."""

import re
from collections.abc import Collection
from decimal import Decimal
from fnmatch import fnmatchcase
from pathlib import PurePosixPath


def parse_selection(expression: str, available: Collection[int]) -> set[int]:
    valid = set(available)
    selected: set[int] = set()
    for token in expression.split(","):
        match = re.fullmatch(r"\s*([0-9]+)(?:\s*-\s*([0-9]+))?\s*", token)
        if match is None:
            raise ValueError("请输入文件编号或范围，例如 1,3-5。")
        start = int(match[1])
        end = int(match[2]) if match[2] is not None else start
        # Bound before expanding so a mistyped huge range cannot exhaust memory.
        if start > end or start < 1 or end > max(valid, default=0):
            raise ValueError(f"无效的文件编号或范围：{token.strip()}")
        numbers = set(range(start, end + 1))
        if not numbers <= valid:
            raise ValueError(f"范围中含有不可选的文件：{token.strip()}")
        selected.update(numbers)
    return selected


def parse_size(value: str) -> int:
    """Parse bytes, decimal SI units, or binary K/M/G and KiB/MiB/GiB."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?I?B|[KMGT])?\s*", value.upper())
    if match is None:
        raise ValueError("大小格式无效，例如 500M、1.5GiB 或 2GB。")
    unit = match[2] or "B"
    exponent = "KMGT".index(unit[0]) + 1 if unit[0] in "KMGT" else 0
    base = 1024 if len(unit) == 1 or "I" in unit else 1000
    size = Decimal(match[1]) * base**exponent
    if size != size.to_integral_value():
        raise ValueError("大小必须对应整数个字节。")
    return int(size)


def filter_files(
    files,
    *,
    selected: set[int] | None = None,
    includes: Collection[str] = (),
    excludes: Collection[str] = (),
    extensions: Collection[str] = (),
    min_size: int | None = None,
    max_size: int | None = None,
    largest: bool = False,
) -> set[int]:
    if min_size is not None and max_size is not None and min_size > max_size:
        raise ValueError("--min-size 不能大于 --max-size。")
    suffixes = {"." + extension.lower().lstrip(".") for extension in extensions}

    def matches(path: str, patterns: Collection[str]) -> bool:
        return any(
            fnmatchcase(path.lower(), pattern.lower())
            or fnmatchcase(PurePosixPath(path).name.lower(), pattern.lower())
            for pattern in patterns
        )

    candidates = [
        file
        for file in files
        if (selected is None or file.number in selected)
        and (not includes or matches(file.path, includes))
        and not matches(file.path, excludes)
        and (not suffixes or PurePosixPath(file.path).suffix.lower() in suffixes)
        and (min_size is None or file.size >= min_size)
        and (max_size is None or file.size <= max_size)
    ]
    if largest and candidates:
        return {max(candidates, key=lambda file: (file.size, -file.number)).number}
    return {file.number for file in candidates}
