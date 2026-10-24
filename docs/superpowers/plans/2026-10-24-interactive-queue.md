# Interactive persistent queue

Spec: Arrow-key task selection, live progress, and keyboard pause/resume/cancel.
Execute inline using the existing executing-plans/TDD workflow, with one fresh final reviewer.
The repository has no initial commit; keep the authorized workspace changes uncommitted and record evidence here.

## Design

- `queue tui` monitors/manages the same SQLite queue as another downloader. It never starts or stops that downloader. `queue run --interactive -j N` owns a runner and keeps it alive while idle, so resuming the last paused task or adding new tasks works without restarting.
- Reuse prompt_toolkit. Up/down, Home/End and PageUp/PageDown navigate stable task IDs; p/r/c pause/resume/cancel the focused ID; q exits; Ctrl+C, SIGINT and EOF exit with 130. No confirmation for cancellation, which retains downloaded files and checkpoints as existing commands do.
- Show state, percent, bytes, speed, peers, focused destination and errors. Poll lightweight snapshots every 0.5 s; persisted progress updates about once per second. Keep focus across refreshes. Escape untrusted text. Handle empty queues, terminal resize, SQLite contention and state races without leaving raw mode behind.
- UI DB operations have a short busy timeout. The integrated runner owns a separate SQLite connection in its own thread. On exit stop/join it and checkpoint workers before returning. The existing OS runner lock still prevents concurrent downloaders. Show startup/runner errors in the panel; never silently take over another runner.
- Require usable stdin/stdout terminals for interactive modes; preserve existing noninteractive commands, configuration and queue-path precedence.

## Tasks / ledger

- [x] Store/runner: RED tests for lightweight snapshots and idle resume/add; implement without changing default runner exit semantics.
- [x] UI: RED input-transport tests for navigation, refresh, controls, errors, escaping and shutdown; implement panel and runner lifecycle.
- [x] CLI/docs/PTY: RED tests for new commands, non-TTY rejection and actual download controls/terminal restoration; integrate and document.
- [x] One independent final review; important fixes RED→GREEN; full pytest, Ruff, build and lock checks.

Pre-flight: snapshot rows carry stable IDs and summary fields only; UI control writes use existing transactional state transitions. The runner's keep-alive mode retains its OS lock while idle, whereas ordinary `queue run` still drains and exits. Each thread creates/closes its own SQLite connection. Native workers remain unchanged and receive the existing cancellation events.

Execution evidence: missing snapshot/keep_alive tests failed before implementation, then all 10 storage/runner tests passed. Missing queue_ui import failed before implementation; seven input transport tests passed, including live changes, focus preservation, state errors, SQLite contention, four exit paths, worker cleanup, and duplicate-runner protection. All four new CLI tests failed before wiring the commands, then passed. New PTY tests initially failed on the missing flag. The first implemented download/pause test exposed a test-harness issue: waiting for DB changes without draining the PTY filled its output buffer, blocking repaint/input while the independent native worker continued. The test now drains output like a real terminal emulator; the local seeded pause/resume download passed in 23.23s. The six-case PTY suite also covers cross-process monitor controls, cancellation, resize, q/Ctrl+C/Ctrl+D/SIGINT, alternate-screen restoration and durable pending state on exit.

Storage/UI/CLI regression subset: 21 passed in 1.97s. Six PTY cases passed in 41.69s. Ruff lint/format passed (46 Python files). Package build, lock validation and both new help entry points passed. Final verification follows below.

Independent review: one Important/P2 finding, no critical/minor or declined findings. Rich's `Console.is_terminal` can be forced true by FORCE_COLOR/TTY_COMPATIBLE even with redirected stdout. A real PTY stdin + piped stdout regression timed out before the fix, proving the panel incorrectly entered interactive mode. Replaced the output guard with `sys.stdout.isatty()`; four combinations (both entry points × both environment variables) now pass and create no database. The earlier full suite was deliberately interrupted after 130 passing tests to apply this fix; it is not counted as final verification. No review findings deferred; no second review needed.

## Final verification

- Final: fixed forced-color bypass of terminal detection — `test_redirected_output_rejected_even_when_rich_forces_color` RED→GREEN (four cases), suite **223/223**.
- `uv run pytest -q`: **223 passed in 307.43s**.
- `uv run ruff check .` and `uv run ruff format --check .`: passed, 46 Python files.
- `uv build` and `uv lock --check`: passed after the review fix.
- Terminal coverage includes local seeded download/pause/resume, separate monitor controls, cancellation, resizing, q/Ctrl+C/Ctrl+D/SIGINT, worker joining and screen/input restoration.
- Verified on macOS ARM64 / Python 3.12. Other platform terminal backends were not exercised on this host.
- All requested work complete; no deferred review findings. Changes remain uncommitted in the existing workspace.
