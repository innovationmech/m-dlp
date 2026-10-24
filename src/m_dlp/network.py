"""Validation shared by the CLI and download engine."""


def validate_interface_name(value: str) -> str:
    """Accept one interface name, never libtorrent's interface-list syntax."""
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(char in ",:[]" or ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("网络接口必须是单个有效名称，不能包含逗号、冒号或控制字符。")
    return value
