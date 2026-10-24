# Interactive file selection

Spec: Arrow navigation, space multi-selection, search, directory selection and live total size.

Use executing-plans inline with TDD and one fresh independent final review. No initial Git commit exists; continue in the authorized workspace, leaving changes uncommitted.

## Design

- Add prompt_toolkit 3.x for cross-platform full-screen input, rendering, resizing and terminal restoration. Keep Rich for ordinary CLI output. A pure selection model owns the tree, filtering, selection and aggregate sizes; the terminal adapter contains no download logic.
- Empty initial selection. Up/down, Home/End, PageUp/PageDown navigate; left/right collapse/expand directories. Space toggles a file or all matching descendants of a directory; directory checkboxes show none/partial/all. `a` selects all current search results, `n` clears those results. Enter submits a nonempty selection; q cancels, Ctrl+C/Ctrl+D abort.
- `/` opens a live case-insensitive full-path substring search. Enter returns focus to the tree; Esc clears search. Preserve hidden selections and show their count with the global selected count/size. Directory actions under a search affect only matching descendants; collapse does not restrict directory selection. Keep ordinary collapsed state when search is cleared.
- Scrollable tree with original file numbers, sizes, directory counts and focused path. Plain formatted text and escaped control characters for torrent-supplied names. No terminal markup/escape injection. Support empty matches, Unicode, long paths and terminal resize.
- Default single-source interactive mode uses the tree when stdin/stdout are terminals with a capable TERM; otherwise fall back to existing numeric input. `--simple-select` explicitly selects numeric input. `--interactive` enables interactive selection after CLI filters. Both require terminal stdin and are incompatible with batch/diagnose modes. Existing explicit selections/filters/list/script behavior stays unchanged unless --interactive is requested.
- Single-source `queue add --interactive` / --simple-select resolves metadata, prompts, then saves explicit file numbers without downloading. Cancelling creates no task. Batch queue input continues to require noninteractive selection.

## Tasks / ledger

- [x] Model tests RED -> implement tree navigation, tri-state toggles, filtered directory actions, totals, Unicode/hidden state -> GREEN.
- [x] Terminal tests RED -> prompt_toolkit bindings, search focus, rendering, empty selection/cancel/EOF and scrolling -> GREEN.
- [x] CLI/PTY tests RED -> default and explicit interaction, numeric fallback, queue add, no pre-confirmation payload, terminal restoration and docs -> GREEN.
- [x] Independent review, fix any important findings with regressions, full pytest/Ruff/build/lock verification.

Pre-flight: CLI filters pass only eligible TorrentFile entries into the selection model; preserved original indices flow through the engine and queue selection string. UI filtering is separate from selection. Input transport tests exercise real key bindings and a PTY checks the CLI boundary, including raw terminal cleanup. Existing queue workers never invoke the interactive UI.

Execution: model and terminal module import failures observed before implementation; six model and ten key-binding/UI tests passed. CLI missing helper/flag failures observed before implementation; eight CLI/fallback/queue integration tests passed. Four actual PTY cases verified selective payload, terminal resize, q/Ctrl+C/Ctrl+D and alternate-screen cleanup. Initial strict termios equality assertion failed only on macOS's kernel-maintained PENDIN bit; an isolated raw_mode/direct tcsetattr experiment reproduced that same bit while all input modes restored. The assertion now masks only PENDIN. All 28 new tests passed in 25.43s; Ruff lint/format checks passed. Dependency lock now contains prompt-toolkit 3.0.53 and wcwidth 0.9.3.

Independent final review: no actionable findings. Reviewed model/search/hidden selections, original indices, terminal text escaping, cancellation, CLI fallback and queue integration. No deferred findings or review fixes. Package build, lockfile validation and both CLI help screens passed.

## Final verification

- `uv run pytest -q`: **200 passed in 265.06s**.
- `uv run ruff check .` and `uv run ruff format --check .`: passed (40 Python files).
- `uv build`, `uv lock --check`, main help and queue-add help: passed.
- Independent review: no actionable findings or deferred issues.
- PTY verification ran on macOS; Windows uses prompt_toolkit's terminal backend but was not exercised on this host.
