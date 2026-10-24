"""A selective magnet-link downloader."""

__version__ = "0.1.0"


def main() -> None:
    from .cli import run

    raise SystemExit(run())
