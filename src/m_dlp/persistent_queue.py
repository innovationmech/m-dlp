"""Bounded foreground runner with durable state and live cross-process control."""

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import replace
from queue import Empty, SimpleQueue
from threading import Event

from .batch import QueueReport, TaskEvent, TaskResult


def run_persistent(
    store, jobs, worker, on_event, *, cancelled=None, ids=None, keep_alive=False
) -> QueueReport:
    if type(jobs) is not int or jobs < 1:
        raise ValueError("同时运行的任务数必须是正整数。")
    cancelled = cancelled if cancelled is not None else Event()
    events = SimpleQueue()
    active = {}
    results = {}
    updates = {}
    last_saved = {}
    tracked = (
        set(ids)
        if ids is not None
        else {
            record.id for record in store.list() if record.state not in ("completed", "cancelled")
        }
    )

    def flush(number):
        if update := updates.pop(number, None):
            store.progress(number, **update)
            last_saved[number] = time.monotonic()

    def drain():
        while True:
            try:
                event = events.get_nowait()
            except Empty:
                return
            number = event.task.number
            if event.kind == "resolved":
                name, total = event.data
                updates.setdefault(number, {}).update(name=name, total=total)
            elif event.kind == "metadata":
                updates.setdefault(number, {}).update(peers=event.data)
            elif event.kind == "progress":
                snapshot = event.data
                updates.setdefault(number, {}).update(
                    completed=snapshot.completed,
                    total=snapshot.total,
                    rate=snapshot.rate,
                    peers=snapshot.peers,
                )
            if time.monotonic() - last_saved.get(number, 0) >= 1:
                flush(number)
            on_event(event)

    with store.runner():
        executor = ThreadPoolExecutor(max_workers=jobs)
        try:
            while True:
                while len(active) < jobs and not cancelled.is_set():
                    record = store.claim(ids)
                    if record is None:
                        break
                    tracked.add(record.id)
                    stopped = Event()
                    # Queue started before submit, preserving UI ordering even
                    # when the worker emits its first event immediately.
                    events.put(TaskEvent(record.task, "started"))
                    future = executor.submit(worker, record, stopped, events.put)
                    active[future] = (record, stopped)
                drain()
                for record, stopped in active.values():
                    if cancelled.is_set() or store.get(record.id).state != "running":
                        stopped.set()
                if not active:
                    if keep_alive and not cancelled.is_set():
                        cancelled.wait(0.1)
                        continue
                    break
                done, _ = wait(active, timeout=0.1, return_when=FIRST_COMPLETED)
                drain()
                for future in done:
                    record, _ = active.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = TaskResult(record.task, "failed", error=str(exc))
                    flush(record.id)
                    final = store.finish(
                        record.id, result.state, error=result.error, interrupted=cancelled.is_set()
                    )
                    result = replace(result, state=final.state, error=final.error)
                    results[record.id] = result
                    on_event(TaskEvent(record.task, "result", result))
        finally:
            # Unexpected UI/DB errors must still checkpoint and join workers
            # before a different process can acquire this queue's runner lock.
            for _, stopped in active.values():
                stopped.set()
            executor.shutdown(wait=True)
            store.recover()
    report = []
    for record in store.list():
        if record.id in tracked:
            result = results.get(record.id, TaskResult(record.task, record.state, record.name))
            report.append(replace(result, state=record.state, error=record.error))
    return QueueReport(tuple(report), cancelled.is_set())
