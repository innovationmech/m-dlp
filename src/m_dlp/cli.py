"""Command line parsing and terminal presentation."""

import argparse
import math
import os
import re
import sqlite3
import sys
from functools import partial
from pathlib import Path
from threading import Event

from rich.console import Console
from rich.filesize import decimal
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table
from rich.text import Text

from . import __version__
from .config import load_defaults
from .limits import parse_rate
from .network import validate_interface_name
from .selection import filter_files, parse_selection, parse_size


def _positive_seconds(value: str) -> float:
    number = _nonnegative_seconds(value)
    if number == 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return number


def _nonnegative_seconds(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("请输入秒数") from exc
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("必须是大于或等于 0 的有限数值")
    return number


def _size(value: str) -> int:
    try:
        return parse_size(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _rate(value: str) -> int:
    try:
        return parse_rate(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _network_interface(value: str) -> str:
    try:
        return validate_interface_name(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _jobs(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("任务数必须是正整数") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("任务数必须是正整数")
    return number


def _pattern(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("匹配模式不能为空")
    return value


def _extension(value: str) -> str:
    if not re.fullmatch(r"\.?[a-zA-Z0-9]+", value):
        raise argparse.ArgumentTypeError("请输入扩展名，例如 mkv；多种类型请重复 --ext")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="m-dlp",
        description="解析磁力链接或本地 .torrent，选择文件并下载。未指定选择参数时显示交互文件树。",
        epilog='示例：m-dlp "magnet:?xt=urn:btih:..." --select 1,3-5 -o ./downloads；'
        "持久化任务管理：m-dlp queue --help",
    )
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("source", nargs="?", help="磁力链接（需加引号）或本地 .torrent 文件路径")
    inputs.add_argument(
        "-i", "--batch-file", type=Path, metavar="FILE", help="批量文件：UTF-8，每行一个磁力链接"
    )
    parser.add_argument(
        "-j", "--jobs", type=_jobs, default=3, metavar="N", help="批量任务的最大并发数（默认：3）"
    )
    parser.add_argument("--version", action="version", version=f"m-dlp {__version__}")
    parser.add_argument("--queue-db", type=Path, metavar="FILE", help="持久化队列数据库路径")
    config = parser.add_mutually_exclusive_group()
    config.add_argument("--config", type=Path, metavar="FILE", help="读取指定 TOML 配置文件")
    config.add_argument("--no-config", action="store_true", help="忽略配置文件及 M_DLP_CONFIG")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("-l", "--list", action="store_true", help="只获取并展示文件列表")
    selection.add_argument(
        "-s", "--select", metavar="NUMBERS", help="文件编号，如 1,3-5（从 1 开始）"
    )
    selection.add_argument("-a", "--all", action="store_true", help="下载所有可选文件")
    selection.add_argument(
        "--interactive", action="store_true", help="交互选择文件，可与筛选条件一起使用"
    )
    selection.add_argument(
        "--simple-select", action="store_true", help="使用编号输入交互选择，适合简单终端"
    )
    selection.add_argument(
        "--resume", action="store_true", help="恢复相同输出目录上次保存的文件选择"
    )
    selection.add_argument(
        "--export-torrent", type=Path, metavar="FILE", help="导出完整资源的 .torrent 元数据后退出"
    )
    filters = parser.add_argument_group("文件筛选（多个条件取交集，匹配不区分大小写）")
    filters.add_argument(
        "--include",
        action="append",
        type=_pattern,
        default=[],
        metavar="GLOB",
        help="包含的路径/文件名模式，可重复；模式之间取并集",
    )
    filters.add_argument(
        "--exclude",
        action="append",
        type=_pattern,
        default=[],
        metavar="GLOB",
        help="排除的路径/文件名模式，可重复",
    )
    filters.add_argument(
        "--ext",
        action="append",
        type=_extension,
        default=[],
        metavar="EXT",
        help="扩展名，例如 mkv，可重复",
    )
    filters.add_argument(
        "--min-size", type=_size, metavar="SIZE", help="最小文件大小，含边界，例如 500M"
    )
    filters.add_argument(
        "--max-size", type=_size, metavar="SIZE", help="最大文件大小，含边界，例如 2GB"
    )
    filters.add_argument("--largest", action="store_true", help="在筛选结果中仅选择最大的一个文件")
    cache = parser.add_argument_group("缓存与恢复")
    cache.add_argument(
        "--cache-dir",
        type=Path,
        metavar="DIR",
        help="缓存目录（可用 M_DLP_CACHE_DIR 环境变量指定）",
    )
    cache_toggle = cache.add_mutually_exclusive_group()
    cache_toggle.add_argument(
        "--no-cache", action="store_true", help="不读取或写入元数据及恢复缓存"
    )
    cache_toggle.add_argument(
        "--cache", dest="no_cache", action="store_false", help="启用缓存，覆盖配置文件"
    )
    parser.set_defaults(no_cache=False)
    cache.add_argument("--recheck", action="store_true", help="忽略快速恢复状态，重新校验已有文件")
    parser.add_argument(
        "--diagnose", action="store_true", help="只探测网络和文件分片可用性，不下载文件内容"
    )
    parser.add_argument(
        "--probe-seconds",
        type=_positive_seconds,
        default=10.0,
        metavar="SECONDS",
        help="诊断观测时长（默认：10）",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path.cwd(),
        metavar="DIR",
        help="输出目录（默认：当前目录）",
    )
    parser.add_argument(
        "--download-limit",
        type=_rate,
        default=0,
        metavar="RATE",
        help="下载上限，单位字节/秒，例如 10M；0 表示不限速（默认）",
    )
    parser.add_argument(
        "--upload-limit",
        type=_rate,
        default=0,
        metavar="RATE",
        help="上传上限，单位字节/秒，例如 1M；0 表示不限速（默认）",
    )
    parser.add_argument(
        "--network-interface",
        type=_network_interface,
        metavar="NAME",
        help="绑定指定网络接口（例如 VPN 接口 utun8）；接口消失时停止下载",
    )
    parser.add_argument(
        "--metadata-timeout",
        type=_positive_seconds,
        default=120.0,
        metavar="SECONDS",
        help="获取元数据的超时秒数（默认：120）",
    )
    parser.add_argument(
        "--stall-timeout",
        type=_nonnegative_seconds,
        default=300.0,
        metavar="SECONDS",
        help="无数据传输的超时秒数，0 表示不限（默认：300）",
    )
    return parser


def _parse_args(argv: list[str] | None = None, parser=None):
    parser = parser or _parser()
    # Parse once before reading config so --help and --version always work.
    args = parser.parse_args(argv)
    converters = {
        "output": Path,
        "cache-dir": Path,
        "queue-db": Path,
        "no-cache": bool,
        "download-limit": parse_rate,
        "upload-limit": parse_rate,
        "network-interface": validate_interface_name,
        "metadata-timeout": _positive_seconds,
        "stall-timeout": _nonnegative_seconds,
        "probe-seconds": _positive_seconds,
        "jobs": _jobs,
    }
    try:
        defaults = load_defaults(args.config, disabled=args.no_config, converters=converters)
    except (ValueError, argparse.ArgumentTypeError) as exc:
        parser.error(str(exc))
    parser.set_defaults(**defaults)
    return parser.parse_args(argv)


def _show_files(console: Console, name: str, files) -> None:
    table = Table(title=Text(name), show_lines=False)
    table.add_column("编号", justify="right", style="cyan")
    table.add_column("大小", justify="right")
    table.add_column("文件路径", overflow="fold")
    for file in files:
        # Text treats torrent-controlled strings as text, never Rich markup.
        table.add_row(str(file.number), decimal(file.size), Text(file.path))
    console.print(table)
    console.print(f"共 {len(files)} 个文件，{decimal(sum(file.size for file in files))}")


def _choose_files(console: Console, numbers: set[int]) -> set[int]:
    while True:
        expression = console.input("选择文件（例如 1,3-5；all 全选；q 退出）：").strip()
        if expression.lower() == "q":
            return set()
        if expression.lower() == "all":
            return numbers
        try:
            return parse_selection(expression, numbers)
        except ValueError as exc:
            console.print(Text(str(exc), style="red"))


def _wants_interactive(args) -> bool:
    return (
        args.interactive
        or args.simple_select
        or not (args.all or args.resume or args.select is not None or _has_filters(args))
    )


def _prompt_files(args, console, name, files) -> set[int]:
    if (
        not args.simple_select
        and sys.stdin.isatty()
        and console.is_terminal
        and os.environ.get("TERM", "").lower() not in {"dumb", "unknown"}
    ):
        from .picker_ui import choose_files

        return choose_files(files, name)
    _show_files(console, name, files)
    return _choose_files(console, {file.number for file in files})


def _has_filters(args) -> bool:
    return bool(
        args.include
        or args.exclude
        or args.ext
        or args.largest
        or args.min_size is not None
        or args.max_size is not None
    )


def _select(args, files, download) -> set[int]:
    selected = None
    if args.select is not None:
        selected = parse_selection(args.select, {file.number for file in files})
    elif args.resume:
        selected = download.saved_selection(args.output)
        if selected is None:
            raise ValueError("没有找到该资源在此输出目录的选择记录，请使用 --select 或 --all。")
    result = filter_files(
        files,
        selected=selected,
        includes=args.include,
        excludes=args.exclude,
        extensions=args.ext,
        min_size=args.min_size,
        max_size=args.max_size,
        largest=args.largest,
    )
    if not result:
        raise ValueError("没有符合条件的文件，请调整筛选条件。")
    return result


def _show_diagnostics(console: Console, snapshot) -> None:
    console.print("下载诊断", style="bold")
    console.print(f"已连接 {snapshot.peers} 个节点，其中 {snapshot.seeds} 个完整做种节点")
    nodes = str(snapshot.dht_nodes) if snapshot.dht_nodes is not None else "等待统计"
    console.print(f"DHT：{'已启用' if snapshot.dht_enabled else '已关闭'}；路由节点：{nodes}")
    if snapshot.trackers:
        table = Table("Tracker", "状态", "详情")
        for tracker in snapshot.trackers:
            table.add_row(Text(tracker.label), tracker.state, Text(tracker.detail))
        console.print(table)
    else:
        console.print("Tracker：链接未指定（可通过 DHT 或显式 peer 寻找节点）")
    for error in snapshot.network_errors:
        console.print(Text(error, style="yellow"))
    if snapshot.files:
        table = Table("编号", "文件", "观测覆盖", "本地已校验", "未观测分片")
        for file in snapshot.files:
            table.add_row(
                str(file.number),
                Text(file.path),
                f"{file.percent:.1f}%",
                decimal(file.local_bytes),
                f"{file.missing_pieces}/{file.total_pieces}",
            )
        console.print(table)
    if snapshot.peers == 0:
        console.print("当前没有已连接节点；资源可能暂时无人在线，也可能是网络发现尚未完成。")
    console.print(
        "覆盖率包含本地已校验分片和当前节点声明持有的分片，不保证最终下载成功。", markup=False
    )


def _resolve_files(args, console, download):
    with console.status("正在读取资源元数据…") as status:
        files = download.resolve(
            args.metadata_timeout,
            on_status=lambda peers: status.update(f"正在获取元数据… 已连接 {peers} 个节点"),
        )
    if download.metadata_cached:
        console.print("已从缓存读取文件列表。")
    return files


def _execute(args, console, download) -> None:
    files = _resolve_files(args, console, download)
    if args.export_torrent is not None:
        download.export_torrent(args.export_torrent)
        console.print(
            Text(f"种子已导出：{args.export_torrent.expanduser().absolute()}", style="green")
        )
        return
    selected = _select(args, files, download)
    shown = [file for file in files if file.number in selected] if _has_filters(args) else files
    if args.list:
        _show_files(console, download.name, shown)
        return
    if args.diagnose:
        _show_files(console, download.name, shown)
        with console.status(f"正在探测网络与分片可用性（{args.probe_seconds:g} 秒）…"):
            snapshot = download.probe(args.probe_seconds, selected)
        _show_diagnostics(console, snapshot)
        return
    if _wants_interactive(args):
        selected = _prompt_files(
            args, console, download.name, tuple(file for file in files if file.number in selected)
        )
    else:
        _show_files(console, download.name, shown)
    if not selected:
        console.print("已取消。")
        return
    total = sum(file.size for file in files if file.number in selected)
    console.print(f"下载 {len(selected)} 个文件，合计 {decimal(total)}")
    with Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TextColumn("{task.fields[rate]} · {task.fields[peers]} 节点"),
        TimeRemainingColumn(),
        console=console,
        disable=not console.is_terminal,
    ) as progress:
        task = progress.add_task("准备下载", total=total, rate="0 B/s", peers=0)

        def update(snapshot):
            progress.update(
                task,
                description=snapshot.state,
                completed=snapshot.completed,
                rate=f"{decimal(snapshot.rate)}/s",
                peers=snapshot.peers,
            )

        download.download(selected, args.output, args.stall_timeout, update, recheck=args.recheck)
    if download.resume_loaded:
        console.print("已使用快速恢复状态。")
    console.print(Text(f"下载完成：{args.output.expanduser().resolve()}", style="green"))


def _batch_worker(task, cancelled, emit, *, args):
    from .batch import TaskEvent, TaskResult
    from .engine import DownloadCancelled, DownloadError, MagnetDownload

    if cancelled.is_set():
        return TaskResult(task, "cancelled")
    options = argparse.Namespace(**vars(args))
    options.output = task.destination
    download = None
    name, error, state = "", "", "completed"
    shown = ()
    diagnostic = None
    try:
        # The user chooses the root, but generated task subdirectories must not
        # be symlinks that merge otherwise isolated torrents or escape it.
        if not args.list and task.destination.is_symlink():
            raise ValueError(f"任务输出目录不能是符号链接：{task.destination}")
        download = MagnetDownload(
            task.source,
            cache_dir=args.cache_dir,
            use_cache=not args.no_cache,
            download_limit=args.download_limit,
            upload_limit=args.upload_limit,
            network_interface=args.network_interface,
            cancelled=cancelled.is_set,
        )
        with download:
            try:
                files = download.resolve(
                    args.metadata_timeout,
                    on_status=lambda peers: emit(TaskEvent(task, "metadata", peers)),
                )
                name = download.name
                selected = _select(options, files, download)
                chosen = tuple(file for file in files if file.number in selected)
                shown = chosen if _has_filters(args) else files
                emit(TaskEvent(task, "resolved", (name, sum(file.size for file in chosen))))
                if not args.list:
                    download.download(
                        selected,
                        task.destination,
                        args.stall_timeout,
                        lambda snapshot: emit(TaskEvent(task, "progress", snapshot)),
                        recheck=args.recheck,
                    )
            except DownloadError:
                if download.handle is not None and download.handle.is_valid():
                    diagnostic = download.diagnostics(download._selected or None)
                raise
    except DownloadCancelled as exc:
        state, error = "cancelled", str(exc)
    except (ValueError, OSError, RuntimeError) as exc:
        state, error = "failed", str(exc)
    return TaskResult(
        task, state, name, error, tuple(download.warnings) if download else (), shown, diagnostic
    )


def _execute_batch(args, console, errors, loaded, *, runner=None) -> int:
    from .batch import cancel_on_sigint, run_queue

    console.print(f"共 {len(loaded.tasks)} 个任务，最多同时运行 {args.jobs} 个；限速按任务生效。")
    if loaded.duplicates:
        console.print(f"已跳过 {loaded.duplicates} 个重复链接。")
    labels = {
        "completed": "成功",
        "failed": "失败",
        "cancelled": "已取消",
        "pending": "未开始",
        "paused": "已暂停",
        "pausing": "暂停中",
        "cancelling": "取消中",
        "running": "运行中",
    }
    rows = {}
    names = {}
    cancelled = Event()
    with (
        cancel_on_sigint(cancelled),
        Progress(
            TextColumn("{task.description}", markup=False),
            BarColumn(),
            DownloadColumn(),
            TextColumn("{task.fields[rate]} · {task.fields[peers]} 节点"),
            TimeRemainingColumn(),
            console=console,
            disable=not console.is_terminal,
        ) as progress,
    ):

        def observe(event):
            number = event.task.number
            if event.kind == "started":
                rows[number] = progress.add_task(
                    f"[{number}] 获取元数据", total=None, rate="0 B/s", peers=0
                )
                if not console.is_terminal:
                    console.print(f"[{number}] 开始（输入文件第 {event.task.line} 行）")
            elif event.kind == "metadata":
                progress.update(rows[number], peers=event.data)
            elif event.kind == "resolved":
                name, total = event.data
                names[number] = name
                progress.update(rows[number], description=f"[{number}] {name}", total=total)
            elif event.kind == "progress":
                snapshot = event.data
                progress.update(
                    rows[number],
                    completed=snapshot.completed,
                    description=f"[{number}] {names.get(number, '')} · {snapshot.state}",
                    rate=f"{decimal(snapshot.rate)}/s",
                    peers=snapshot.peers,
                )
            elif event.kind == "result":
                result = event.data
                if number in rows:
                    progress.remove_task(rows.pop(number))
                console.print(
                    Text(
                        f"[{number}] {labels[result.state]}：{result.name or result.task.key[:12]}"
                    )
                )
                if result.error:
                    errors.print(
                        Text(
                            f"[{number}] {result.error}",
                            style="yellow" if result.state == "cancelled" else "red",
                        )
                    )
                if result.diagnostics is not None:
                    _show_diagnostics(errors, result.diagnostics)
                if args.list and result.state == "completed":
                    _show_files(console, result.name, result.files)
                for warning in result.warnings:
                    errors.print(Text(f"[{number}] 提示：{warning}", style="yellow"))

        report = (runner or run_queue)(
            loaded.tasks,
            args.jobs,
            partial(_batch_worker, args=args),
            observe,
            cancelled=cancelled,
        )
    table = Table("任务", "输入行", "状态", "资源", "详情", title="批量任务结果")
    counts = dict.fromkeys(labels, 0)
    for result in report.results:
        counts[result.state] += 1
        detail = result.error
        if result.state == "completed":
            detail = f"{len(result.files)} 个文件" if args.list else str(result.task.destination)
        table.add_row(
            str(result.task.number),
            str(result.task.line),
            labels[result.state],
            Text(result.name or result.task.key[:12]),
            Text(detail),
        )
    console.print(table)
    console.print("，".join(f"{labels[state]} {count}" for state, count in counts.items()))
    if report.interrupted:
        console.print(
            "已停止队列，已下载的数据会保留；使用 m-dlp queue run 继续持久化任务。"
            if runner
            else "已停止队列，已下载的数据会保留；使用相同批量文件和输出目录可继续下载。"
        )
        return 130
    return 1 if counts["failed"] else 0


def run(argv: list[str] | None = None, *, enqueue_only=False) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not enqueue_only and argv[:1] == ["queue"]:
        from .queue_cli import run as run_management

        return run_management(argv[1:])
    parser = _parser()
    if enqueue_only:
        parser.prog = "m-dlp queue add"
    args = _parse_args(argv, parser)
    if args.interactive or args.simple_select:
        if args.batch_file is not None or args.diagnose:
            parser.error("交互选择仅支持单个下载资源，不能与批量模式或 --diagnose 一起使用。")
        if not sys.stdin.isatty():
            parser.error("交互选择需要终端输入；脚本请使用 --select、--all 或筛选条件。")
    if enqueue_only and (args.list or args.diagnose or args.export_torrent is not None):
        parser.error("queue add 仅添加下载任务，不能使用 --list、--diagnose 或 --export-torrent。")
    if args.batch_file is not None and (args.diagnose or args.export_torrent is not None):
        parser.error("批量模式支持下载和 --list，暂不支持 --diagnose 或 --export-torrent。")
    if args.export_torrent is not None and (args.diagnose or _has_filters(args)):
        parser.error("--export-torrent 导出完整资源，不能与 --diagnose 或筛选条件一起使用。")
    if args.list and args.diagnose:
        parser.error("--list 与 --diagnose 不能同时使用。")
    if args.resume and args.no_cache:
        parser.error("--resume 需要缓存，不能与 --no-cache 一起使用。")
    if args.min_size is not None and args.max_size is not None and args.min_size > args.max_size:
        parser.error("--min-size 不能大于 --max-size。")
    if not (
        args.list
        or args.export_torrent is not None
        or args.diagnose
        or args.all
        or args.interactive
        or args.simple_select
        or args.resume
        or args.select is not None
        or _has_filters(args)
    ) and (enqueue_only or args.batch_file is not None or not sys.stdin.isatty()):
        parser.error(
            "批量或非交互模式请指定 --select、--all、--list、--resume、筛选条件、"
            "--diagnose 或 --export-torrent。"
        )
    console = Console()
    errors = Console(stderr=True)
    try:
        from .engine import DownloadError, MagnetDownload
    except ImportError:
        errors.print(
            "无法加载 libtorrent。请使用 Python 3.12 并运行 uv sync 安装依赖。", markup=False
        )
        return 1

    download = None
    try:
        if args.batch_file is not None:
            from .batch import load_batch

            try:
                loaded = load_batch(args.batch_file, args.output)
            except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
                parser.error(f"无法读取批量任务：{exc}")
            if args.list:
                return _execute_batch(args, console, errors, loaded)
            from .queue_cli import enqueue

            return enqueue(args, console, errors, loaded, start=not enqueue_only)
        if enqueue_only:
            from .queue_cli import enqueue

            return enqueue(args, console, errors)
        with MagnetDownload(
            args.source,
            cache_dir=args.cache_dir,
            use_cache=not args.no_cache,
            download_limit=args.download_limit,
            upload_limit=args.upload_limit,
            network_interface=args.network_interface,
        ) as download:
            try:
                _execute(args, console, download)
            except DownloadError:
                if download.handle is not None and download.handle.is_valid():
                    _show_diagnostics(errors, download.diagnostics(download._selected or None))
                raise
        return 0
    except (KeyboardInterrupt, EOFError):
        errors.print("已取消，已下载的数据会保留；使用相同输出目录可继续下载。", markup=False)
        return 130
    except ValueError as exc:
        errors.print(Text(f"参数错误：{exc}", style="red"))
        return 2
    except (DownloadError, OSError, RuntimeError, sqlite3.Error) as exc:
        errors.print(Text(f"下载失败：{exc}", style="red"))
        return 1
    finally:
        if download is not None:
            for warning in download.warnings:
                errors.print(Text(f"提示：{warning}", style="yellow"))
