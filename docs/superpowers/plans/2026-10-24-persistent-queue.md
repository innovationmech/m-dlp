# Persistent queue and task management

Spec: Persist the existing batch queue and support viewing, pausing, resuming and cancelling downloads.

Use executing-plans inline, TDD and one fresh independent final review. The repository has no initial commit; keep the authorized changes in the current workspace without committing the existing scaffold.

## Design

- SQLite (standard library) stores stable integer IDs, source, immutable local torrent bytes, absolute destination/cache paths, download options, status, progress and timestamps. Queue storage is separate from disposable resume caches.
- Existing batch downloads automatically enqueue and run their submitted IDs. List-only previews remain ephemeral. `queue add SOURCE/-i FILE` enqueues without downloading; explicit selection is required. `queue run -j N` drains eligible pending tasks, including additions while it is running, then exits. Single-source interactive downloads keep their current interface; use queue add to manage them.
- `queue list`, `queue show ID`, `queue pause ID...`, `queue resume ID...`, `queue cancel ID...`; list/show offer JSON. Resume retries failed tasks as well as paused tasks. Cancel retains payload but is terminal; add again to retry cancelled/completed tasks.
- `--queue-db`, TOML queue-db, M_DLP_QUEUE_DB and a platform data directory select durable storage. Normalized absolute paths survive cwd/config changes. Local torrent contents survive removal of the input file.
- A process-held OS file lock permits one runner per database. Transactions serialize claims and management commands. Another CLI can request pause/cancel; cooperative cancellation checkpoints before marking final status. Resuming during pause waits for cleanup before another attempt. Ctrl+C requeues active work and preserves paused/cancelled intentions. Abrupt death releases the lock; next runner recovers active records without stealing a live runner.
- Persist progress at most once per second per active task, with final updates flushed. No background daemon: resume marks pending; run starts it when no runner is active. Metadata, transfer and cleanup all count against jobs.
- Destination reservations prevent duplicate concurrent writes within one queue. Re-adding unfinished tasks reuses their ID and saved options. Separate queue databases must use separate output directories.

## Tasks / ledger

- [x] Durable store tests RED, then transactional state machine, identity/reservations, lock and crash recovery GREEN.
- [x] Persistent scheduler tests RED, then dynamic bounded scheduling, pause/resume/cancel races and cleanup GREEN.
- [x] CLI/integration tests RED, then commands, automatic batch persistence, config, local torrent snapshot and docs GREEN.
- [x] Independent review, regressions for important findings, complete pytest/Ruff/build verification.

Pre-flight: scheduler and store share active/state transitions; the active flag reserves a slot/destination until checkpoint cleanup has finished, even when resume is requested during pause. CLI uses the existing batch worker and presentation; persistent runner converts stored records back to BatchTask IDs.

Execution: Store and scheduler import failures reproduced before implementation; 8 core tests green. CLI missing-command failures reproduced first; configuration regression revealed argparse subparser defaults override parent defaults, corrected by applying configuration directly to the selected subparser. Queue/config/batch suite: 56 passed in 52.09s. Updated the existing SIGINT assertion to expect the interrupted active task to return to durable pending along with unstarted tasks. Real payload pause/checkpoint/resume test passed in 35.27s, with native resume checkpoint present and final file bytes verified.

Independent final review: no actionable findings. Reviewed state transitions, cleanup reservation, OS locking and crash recovery, progress persistence, immutable torrent snapshots, saved paths/options and legacy integration. No review fixes or deferred findings. Ruff lint/format, package build, lockfile validation and CLI help checks passed.

Full-suite discovery: first run had 170 passed / 1 failed (227.53s), an existing single-torrent download reported `invalid torrent handle used` while switching handles. Inspection identified the asynchronous remove -> is_valid -> status race, also documented in [libtorrent's torrent_handle lifetime warning](https://libtorrent.org/reference-Torrent_Handle.html). A deterministic removal-wait regression failed at `_alerts` status lookup before the fix. Removal waits now skip that status query while retaining cancellation and error-alert handling. Regression plus the originally failing integration test, cancellation tests and real queued pause/resume: 5 passed in 43.00s.

## Final verification

- `uv run pytest -q`: **172 passed in 239.21s**, including the deterministic handle-removal regression.
- `uv run ruff check .` and `uv run ruff format --check .`: passed.
- `uv build` and `uv lock --check`: passed after the engine fix.
- Independent queue review: no actionable findings; no deferred issues.
- Integration tests use local seeders, subprocess management commands and real payload verification. Windows lock branch is implemented but not exercised on this macOS host.
