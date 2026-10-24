# Queue search, state filters and bulk controls

Spec: Add search, state filtering and space multi-selection to the existing live queue UI; support bulk pause/resume/cancel.
Continue the executing-plans/TDD workflow in the authorized workspace, with one fresh independent final reviewer. No initial Git commit exists; leave all work uncommitted and keep the ledger here.

## Design

- A pure queue view model owns snapshots, case-insensitive substring search across name/ID/hash/destination, exact state filter, visible rows, stable cursor and selected IDs.
- `/` focuses a live search field; Enter returns to the list. `f` opens a scrollable status chooser (all plus every persisted state); arrows navigate, Enter applies, Esc cancels. Outside the chooser, Esc clears search and status filters without clearing selections. Typing in the search field never invokes task-control shortcuts.
- Space toggles the focused task; `a` selects visible results, `n` clears all selections. Search/filter/refresh preserve selected IDs, including hidden results; the UI shows visible/total counts, selected count, hidden selected count and current state filter. Removed IDs are pruned on a successful refresh. Preserve selection after successful actions for pause→resume workflows.
- p/r/c target all selected IDs, including hidden ones; without selection they target the focused task. Use the existing QueueStore.control transaction: unsupported state or race rejects the entire action, keeps selections and displays an error. Do not silently skip tasks or partially apply a batch. Global queue totals remain global when the list is filtered.
- Reuse existing prompt_toolkit input/layout patterns and bounded DB polling. Keep worker lifetime, ownership, queue CLI syntax, noninteractive management and file retention unchanged. Support empty results, Unicode, narrow terminals, resize and cancellation.

## Tasks / ledger

- [x] Model: RED tests for combined search/state filtering, stable focus, hidden selections and refresh; implement model.
- [x] UI: RED input tests for search focus, chooser, Space/a/n, bulk controls and transaction failures; integrate model, toolbar and bindings.
- [x] End-to-end/docs: real PTY multi-select/filter/bulk controls and terminal restore; update usage docs.
- [x] Independent final review, important fixes RED→GREEN, full pytest/Ruff/build/lock verification.

Pre-flight: TaskSnapshot already supplies every searchable field, so polling stays lightweight. QueueStore.control already accepts multiple IDs and rolls back all changes on an unsupported state; the UI passes stable selected IDs without prefiltering by possibly stale state. Search and status filters affect visibility only, not the scope of selected IDs. Existing public panel row/current accessors remain compatible with the UI tests; the download runner is unchanged.

Execution: model tests failed on the missing QueueView import, then all four passed. Four new input-transport tests failed before UI integration, then passed after implementation. The state-race fixture initially tried to finish an unclaimed pending task; the existing runner correctly preserves pending in that case, so the fixture now claims task 2 before finishing it and asserts its completed state before the UI command. Additional tests cover chooser focus isolation/last-state scrolling and Ctrl+C/Ctrl+D from both search and filter contexts. The new PTY case verifies real search/filter/selection keys, all three bulk actions, hidden selections, single-task fallback, resize, terminal restoration and retained partial files. Its first raw-output assertion was corrected to wait for the control action before forcing a complete resize redraw; incremental ANSI updates do not contain the entire rewritten counter as a contiguous string.

Targeted model/UI/CLI/PTY regression subset: **25 passed in 2.19s**. Ruff lint/format passed (51 Python files); package build and lock validation passed. No download-engine or persistence changes were needed.

Independent final review: no actionable findings and no declined items within feature scope. Reviewed model refresh/selection identity, search/chooser input isolation, atomic bulk control, hidden selections, fallback behavior, resize and terminal restoration. No review fixes or deferred findings.

## Final verification

- `uv run pytest -q`: **237 passed in 317.85s**.
- `uv run ruff check .` and `uv run ruff format --check .`: passed, 51 Python files.
- `uv build` and `uv lock --check`: passed.
- Four model tests, nine input-transport tests and one real-terminal bulk workflow added; previous native download/queue/terminal tests remain green.
- Verified on macOS ARM64 / Python 3.12. Other operating-system terminal backends were not exercised on this host.
- All requested features complete; no deferred findings. Changes remain uncommitted in the existing workspace.
