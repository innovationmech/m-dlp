# Persistent queue priority and ordering

Spec: Add priorities and sorting to the queue, with interactive move/top actions and priority-based dispatch.
Continue inline with the existing executing-plans/TDD workflow and one fresh final reviewer. No initial Git commit exists; use the authorized workspace and leave changes uncommitted.

## Design

- SQLite schema v2 adds priority (0 low, 1 normal default, 2 high), pinned boolean and integer position. Transactional v1 migration assigns existing ID order and normal/unpinned defaults without changing payload, options, status or checkpoints. New tasks append; duplicate unfinished tasks retain their existing scheduling settings.
- One canonical order for snapshots/list/claim: pinned first, then priority descending, then position ascending, then ID. Dispatch still claims only pending/inactive tasks within the runner's ID scope. Changes take effect at the next available slot without preempting running work.
- Transactional operations validate all IDs before writes. Priority changes append affected tasks to the destination group; unchanged tasks do not move. Pin moves targets to the front of their pinned priority group; unpin appends to the unpinned group. Toggle pins all targets unless all are already pinned. Manual up/down moves selected blocks one neighbor within each same-pin/same-priority group; top/bottom stable-partitions each group. All operations preserve relative selected order and task IDs and persist across restart.
- TUI `K`/`J` move up/down; `t` toggles pin; `+`/`-` raises/lowers priority. Apply to all selected IDs (including hidden selections), otherwise focused ID. Reordering uses the full queue, so filtered-out neighbors still count; show this in help/docs. Priority and pin markers remain visible in compact rows; preserve focus/selection by ID on refresh. Search/chooser input isolation and existing controls remain intact.
- Script parity: `queue priority ID... --level low|normal|high`, `queue move ID... --direction up|down|top|bottom`, `queue pin ID... [--off]`. List/show and JSON expose priority, pinned and position. No new dependency or download-engine behavior change.

## Tasks / ledger

- [x] Persistence/dispatch: RED tests for real v1 migration, ordering, atomic movement, priority/pin changes, dedup and reopen; implement schema and store methods.
- [x] Scheduling: prove changed order at the next free slot, no preemption and preserved runner scope/concurrency.
- [x] TUI/CLI: RED tests for move/pin/priority keys, filtered multi-selection, focus guards and CLI persistence; implement controls/display and docs; PTY smoke.
- [x] One independent review, important fixes RED→GREEN, full pytest/Ruff/build/lock verification.

Pre-flight: QueueView consumes store snapshot order and already retains focused/selected IDs; no separate display sort is needed. Store.claim is the only dispatch ordering point, so persistent and scoped batch runners both inherit the new order. Existing TaskSnapshot fixture construction remains compatible through default values on appended fields. All reorder mutations share BEGIN IMMEDIATE with claims to avoid lost updates or double dispatch.

Task 1: complete — `tests/test_queue_order.py` first failed 10 tests (schema remained v1 and new operations absent), then the store/snapshot/runner group passed 20 tests. Migration uses the real v1 schema created independently in the test.

Task 2: complete — a live runner with one occupied slot is reordered through a second connection; its start sequence becomes 1,3,4,2 while pinned out-of-scope task 5 remains pending, and task 1 is not interrupted.

Task 3: complete — new UI/CLI group first failed 3 tests (unbound keys and missing command), then passed. Added PTY coverage for actual CLI keys, 12×60 compact pin/priority display, terminal restoration, restart and dispatch order. Focused verification: `uv run pytest -q tests/test_queue_order.py tests/test_queue_order_ui.py tests/test_queue_order_cli.py tests/test_queue_order_pty.py tests/test_queue_ui.py tests/test_queue_ui_bulk.py tests/test_queue_bulk_pty.py tests/test_queue_store.py tests/test_queue_snapshot.py tests/test_persistent_queue.py` → 44 passed in 5.17s. Ruff checks and formatting passed for 56 Python files. README documents commands, keys, group boundaries, hidden selections, schema upgrade and non-preemptive dispatch.

Final review: fresh reviewer `/root/review_queue_priority_order` (gpt-6-astra, high) found no Critical, Important or Minor issues; Declined to judge empty. Its independent probes passed: 1,000 randomized operations against an ordering model; rollback after injected reorder and migration failures; four concurrent writers/claimers claiming 50 tasks exactly once; eight concurrent initial opens/migrations. No review fixes needed. Build and lock verification: `uv build && uv lock --check` passed.

Final verification: `uv run pytest -q` → **254 passed in 306.72s**; log `/tmp/m-dlp-queue-priority-tests.log`. `uv run ruff check . && uv run ruff format --check .` passed (56 files). Working files preserved in the existing repository, uncommitted as planned; no integration or workspace cleanup requested.
