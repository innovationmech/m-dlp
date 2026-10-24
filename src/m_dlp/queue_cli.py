"""Persistent queue commands, sharing the existing download worker and renderer."""

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from rich.console import Console
from rich.filesize import decimal
from rich.table import Table
from rich.text import Text

from .batch import BatchInput, BatchTask
from .cache import default_cache_dir, hash_keys
from .persistent_queue import run_persistent
from .queue_store import PRIORITIES, STATES, QueueStore
from .source import TorrentSource

LABELS = {
    "pending": "等待运行",
    "running": "运行中",
    "pausing": "暂停中",
    "paused": "已暂停",
    "cancelling": "取消中",
    "cancelled": "已取消",
    "completed": "已完成",
    "failed": "失败",
}
PRIORITY_LABELS = {0: "低", 1: "普通", 2: "高"}
OPTION_KEYS = (
    "select",
    "all",
    "resume",
    "include",
    "exclude",
    "ext",
    "min_size",
    "max_size",
    "largest",
    "no_cache",
    "recheck",
    "download_limit",
    "upload_limit",
    "network_interface",
    "metadata_timeout",
    "stall_timeout",
)


def _saved_options(args):
    options = {key: getattr(args, key) for key in OPTION_KEYS}
    options["cache_dir"] = str((args.cache_dir or default_cache_dir()).expanduser().resolve())
    options["list"] = False
    return options


def _worker(record, cancelled, emit):
    from .cli import _batch_worker

    args = argparse.Namespace(**{"network_interface": None, **record.options})
    args.cache_dir = Path(args.cache_dir)
    if record.torrent is None:
        return _batch_worker(record.task, cancelled, emit, args=args)
    # A durable copy lives in SQLite. Only this attempt's temporary input is
    # deleted; output and native resume checkpoints use the original destination.
    with TemporaryDirectory(prefix="m-dlp-queued-torrent-") as directory:
        path = Path(directory) / "input.torrent"
        path.write_bytes(record.torrent)
        task = replace(record.task, source=str(path))
        return _batch_worker(task, cancelled, emit, args=args)


def _execute(store, args, console, errors, *, ids=None, duplicates=0):
    from .cli import _execute_batch

    tasks = tuple(
        record.task
        for record in store.list()
        if (record.id in ids if ids is not None else record.state not in ("completed", "cancelled"))
    )
    console.print(Text(f"队列数据库：{store.path}"))

    def runner(tasks, jobs, worker, on_event, *, cancelled):
        return run_persistent(store, jobs, _worker, on_event, cancelled=cancelled, ids=ids)

    return _execute_batch(args, console, errors, BatchInput(tasks, duplicates), runner=runner)


def enqueue(args, console, errors, loaded=None, *, start=False):
    torrent = None
    if loaded is None:
        if args.interactive or args.simple_select:
            source, selected = _interactive_source(args, console, errors)
            if not selected:
                console.print("已取消，未添加任务。")
                return 0
            args = argparse.Namespace(**vars(args))
            args.select = ",".join(map(str, sorted(selected)))
        else:
            source = TorrentSource(args.source)
        keys = hash_keys(source.parameters().info_hashes)
        if not keys:
            raise ValueError("资源缺少有效的 info hash。")
        key = keys[0]
        torrent = source.data
        value = args.source if source.magnet else str(Path(args.source).expanduser().resolve())
        task = BatchTask(1, 1, value, key, args.output.expanduser().absolute() / key)
        loaded = BatchInput((task,), 0)
    options = _saved_options(args)
    with QueueStore(args.queue_db) as store:
        records = store.add([(task, options, torrent) for task in loaded.tasks])
        console.print("已保存任务 ID：" + ", ".join(str(record.id) for record in records))
        console.print("重复的未完成任务沿用已有 ID、下载参数和状态。")
        if start:
            return _execute(
                store,
                args,
                console,
                errors,
                ids={r.id for r in records},
                duplicates=loaded.duplicates,
            )
        console.print(Text(f"队列数据库：{store.path}"))
        console.print("使用 m-dlp queue run 启动下载；自定义数据库请传入相同的 --queue-db。")
        return 0


def _interactive_source(args, console, errors):
    from .cli import _prompt_files, _resolve_files, _select, _show_diagnostics
    from .engine import DownloadError, MagnetDownload

    download = MagnetDownload(
        args.source,
        cache_dir=args.cache_dir,
        use_cache=not args.no_cache,
        download_limit=args.download_limit,
        upload_limit=args.upload_limit,
        network_interface=args.network_interface,
    )
    try:
        with download:
            try:
                files = _resolve_files(args, console, download)
                candidates = _select(args, files, download)
                selected = _prompt_files(
                    args,
                    console,
                    download.name,
                    tuple(file for file in files if file.number in candidates),
                )
                # Reuse the source read by this resolver, including immutable
                # local torrent bytes, so chosen indices match the queued copy.
                return download._source, selected
            except DownloadError:
                if download.handle is not None and download.handle.is_valid():
                    _show_diagnostics(errors, download.diagnostics())
                raise
    finally:
        for warning in download.warnings:
            errors.print(Text(f"提示：{warning}", style="yellow"))


def _show(console, records, *, detail=False):
    table = Table("ID", "置顶", "优先级", "状态", "资源", "进度", "速度 / 节点", "输出目录")
    for record in records:
        table.add_row(
            str(record.id),
            "是" if record.pinned else "否",
            PRIORITY_LABELS[record.priority],
            LABELS[record.state],
            Text(record.name or record.task.key[:12]),
            f"{decimal(record.completed)} / {decimal(record.total)}",
            f"{decimal(record.rate)}/s · {record.peers}",
            Text(str(record.task.destination)),
        )
    console.print(table)
    if not records:
        console.print("没有任务。")
    if detail:
        for record in records:
            console.print(Text(f"资源 hash：{record.task.key}"))
            console.print(Text(f"错误：{record.error or '无'}"))
            console.print(Text("下载参数：" + json.dumps(record.options, ensure_ascii=False)))


def run(argv):
    from .cli import _jobs, _parse_args
    from .cli import run as download_cli

    if argv[:1] == ["add"]:
        return download_cli(argv[1:], enqueue_only=True)
    parser = argparse.ArgumentParser(prog="m-dlp queue", description="持久化下载任务管理")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("add", help="添加磁力链接、本地种子或批量文件；queue add --help 查看选项")
    for name, help_text in (
        ("list", "查看任务列表"),
        ("show", "查看任务详情"),
        ("tui", "交互查看和管理任务，不启动下载"),
        ("run", "在前台运行等待中的任务，队列排空后退出"),
        ("pause", "暂停任务，保留数据"),
        ("resume", "恢复暂停任务或重试失败任务"),
        ("cancel", "取消任务，保留数据"),
        ("priority", "设置任务优先级：low / normal / high"),
        ("move", "在相同置顶状态、相同优先级组内调整顺序"),
        ("pin", "置顶任务；--off 取消置顶"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--queue-db", type=Path, metavar="FILE")
        config = command.add_mutually_exclusive_group()
        config.add_argument("--config", type=Path, metavar="FILE")
        config.add_argument("--no-config", action="store_true")
        if name in {"list", "show"}:
            command.add_argument(
                "--json", action="store_true", help="输出 JSON（状态使用英文名称）"
            )
        if name == "list":
            command.add_argument("--state", choices=STATES, help="按状态筛选")
        if name in {"show", "pause", "resume", "cancel", "priority", "move", "pin"}:
            command.add_argument(
                "ids", type=_jobs, nargs=1 if name == "show" else "+", metavar="ID"
            )
        if name == "priority":
            command.add_argument("--level", choices=PRIORITIES, required=True)
        if name == "move":
            command.add_argument(
                "--direction", choices=("up", "down", "top", "bottom"), required=True
            )
        if name == "pin":
            command.add_argument("--off", action="store_true", help="取消置顶，移到原优先级组末尾")
        if name == "run":
            command.add_argument("-j", "--jobs", type=_jobs, default=3)
            command.add_argument(
                "--interactive", action="store_true", help="交互运行队列，空闲时等待添加或恢复任务"
            )
    command_name = parser.parse_args(argv).command
    # Defaults belong to the selected subparser; parent defaults would be
    # overwritten by that subparser's own default None during parsing.
    args = _parse_args(argv[1:], commands.choices[command_name])
    args.command = command_name
    console, errors = Console(), Console(stderr=True)
    try:
        if args.command == "tui" or getattr(args, "interactive", False):
            if (
                not sys.stdin.isatty()
                or not sys.stdout.isatty()
                or os.environ.get("TERM", "").lower() in {"dumb", "unknown"}
            ):
                parser.error("队列面板需要可交互终端；脚本请使用 queue list/pause/resume/cancel。")
            from .queue_ui import run_panel

            return run_panel(args.queue_db, jobs=args.jobs if args.command == "run" else None)
        with QueueStore(args.queue_db) as store:
            if args.command == "run":
                args.list = False
                return _execute(store, args, console, errors)
            if args.command == "list":
                records = [r for r in store.list() if args.state is None or r.state == args.state]
            elif args.command == "show":
                records = [store.get(args.ids[0])]
            elif args.command == "priority":
                records = store.set_priority(args.ids, PRIORITIES[args.level])
            elif args.command == "move":
                records = store.move(args.ids, args.direction)
            elif args.command == "pin":
                records = store.pin(args.ids, not args.off)
            else:
                records = store.control(args.ids, args.command)
            if getattr(args, "json", False):
                result = (
                    records[0].public() if args.command == "show" else [r.public() for r in records]
                )
                print(json.dumps(result, ensure_ascii=False))
            else:
                _show(console, records, detail=args.command == "show")
            if args.command == "resume":
                console.print("任务已加入等待队列；若无运行中的下载进程，请执行 m-dlp queue run。")
            return 0
    except (KeyboardInterrupt, EOFError):
        return 130
    except ValueError as exc:
        errors.print(Text(f"参数错误：{exc}", style="red"))
        return 2
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        errors.print(Text(f"队列操作失败：{exc}", style="red"))
        return 1
