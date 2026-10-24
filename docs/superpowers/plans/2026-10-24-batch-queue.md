# Batch downloads and task queue implementation plan

> Use superpowers:executing-plans for implementation and superpowers:requesting-code-review for independent final review.

**Goal:** Read multiple magnets from a UTF-8 file and bound concurrent tasks with --jobs.

**Architecture:** A bounded ThreadPoolExecutor runs independent existing MagnetDownload sessions. A thread-safe event queue keeps terminal rendering on the main thread. A shared cancellation event lets native polling loops exit and save resume data. A batch module owns file validation, identity deduplication, stable output folders and scheduling.

**Tech stack:** Existing Python 3.12, libtorrent, Rich, pytest; standard-library concurrent.futures/threading/queue. No new dependencies.

**Spec:** User requests batch downloads from a file of magnet links and configurable simultaneous downloads. Interface choices below are implementation defaults, preserving existing single-source behavior.

## Constraints and review focus

- --batch-file/-i FILE and positional source are mutually exclusive and one is required. --jobs/-j defaults to 3 and accepts positive integers, including config jobs.
- UTF-8 with optional BOM, one magnet per line; trim outer whitespace, skip blank/comment lines starting with #. Validate all lines before network activity; report line numbers. Deduplicate by known overlapping v1/v2 info hashes, retaining the first occurrence.
- Explicit --all, --select, --resume, filters or --list are required even on a terminal. Selection/filter options apply to each resource; failures are independent. Batch --diagnose and --export-torrent are rejected as unsupported modes.
- Each task uses <output>/<canonical-info-hash>/ to prevent same-name torrents overwriting each other. Preserve resume paths across list order changes. No download folder for --list.
- FIFO dispatch, at most jobs active sessions including metadata resolution and cleanup. Failure frees a slot, continues remaining jobs, and gives exit 1; success exit 0, invalid input exit 2, interrupted exit 130.
- Limits apply per task, documented explicitly. No payload before selection. Duplicate magnets cannot concurrently modify the same destination.
- Ctrl+C stops dispatch, signals active workers, checkpoints their downloads, and identifies not-started tasks. Unexpected presentation errors must also cancel workers rather than hang on shutdown.
- Worker output is marshalled to the main thread, torrent-controlled names treated as plain text. Show per-task progress and final success/failure/cancellation summary.

## Tasks

- [x] Batch parsing and queue: add tests for BOM/comments, malformed input preflight, deduplication, output stability, concurrency bound, continued scheduling after failure and cancellation. Implement batch.py and verify.
- [x] Cooperative engine cancellation: add tests during metadata and active downloads, including saved selection and subsequent recovery. Implement cancel checks without interrupting final checkpoints.
- [x] CLI: implement --batch-file, --jobs/config, explicit batch selection, threaded workers, progress events and final report. Test real local swarms, same-name resource isolation, error continuation, offline batch listing, resume, and SIGINT cleanup.
- [x] Update README/config example, run full pytest/Ruff/build, independent review and corrections.

The user authorized implementation. No additional approval gate; routine interface decisions are stated in chat. Repository has no initial commit, so leave all changes in the working directory.


## Execution notes

- Batch/engine core: 13 tests passed. CLI/options: initial 37/38 passed; the remaining local-peer continuation test used a 2-second metadata timeout, and direct stderr confirmed both resources timed out. Aligning it with the existing 10-second integration-test tolerance made all 25 new feature tests pass.
- Independent review found a SIGINT race during delivery of the started UI event. A deterministic regression raises real SIGINT via a trace hook after the event is dequeued; it reproduced KeyError before the fix. Batch rendering now uses a scoped SIGINT handler that sets cancellation rather than unwinding state updates, and restores the previous signal handler.
- The signal regression plus actual subprocess SIGINT, scheduler and engine cancellation tests passed together: 15 tests. Final full-suite verification passed.


## Final verification

- `uv run pytest -q`: 153 passed in 180.51 seconds.
- Ruff lint and formatting checks passed; `uv build` and `uv lock --check` passed.
- Independent review's SIGINT event-delivery race was reproduced with a failing regression, fixed, and included in the green full suite. No unresolved review findings.
- Validation uses local seeders and trackers; real public swarm availability remains dependent on the resource and network.
