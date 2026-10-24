# Download Enhancements Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for implementation and superpowers:requesting-code-review for the final independent review.

**Goal:** Implement the three user-approved enhancements: cached metadata/fast resume, smart file filtering, and download diagnostics.

**Architecture:** Keep terminal presentation in cli.py and transfer lifecycle in engine.py. Add cache.py for atomic local state storage and diagnostics.py for network observations and file coverage calculations; extend selection.py with pure filtering functions.

**Tech Stack:** Python 3.12/3.13, existing libtorrent 2.0.x, Rich, argparse, pytest, Ruff. No additional runtime dependencies.

**Spec:** The user's approval of the first three recommendations in this chat; concrete interface decisions below are implementation defaults.

## Global Constraints

- No file payload before selection, including cached metadata and diagnostic mode.
- Preserve existing CLI syntax and selective-download behavior.
- Cache automatically; isolate resume data by info hash and canonical output directory.
- Persist checkpoints periodically and during orderly exit; corrupt/stale caches fall back safely.
- Filters: repeatable --include/--exclude/--ext, inclusive --min-size/--max-size, --largest (one file, lowest number breaks ties).
- Patterns match case-insensitively against basename or torrent-relative path; includes OR, then exclusions/size bounds AND, largest last. Filters can narrow --select/--all/--resume; an empty result is an error.
- --diagnose probes without payload, supports filters/selection, and reports current observations rather than guaranteeing availability.
- --resume restores the saved selection for the given output directory; --recheck bypasses fast resume; --cache-dir and --no-cache control caching.

## Review Focus

- Cached data must never change the requested torrent identity, destination, or selection.
- Changing/deleting downloaded files must invalidate fast resume; preserve safe rechecking.
- Canceled and failed downloads must save state without masking the original error.
- Piece coverage must account for file boundaries, padding and empty files, and never label absent peers as permanently unavailable.
- Noninteractive filters/diagnostics must work, and test cache data must stay isolated from the user's cache.

### Task 1: Smart file filtering

**Files:** selection.py, tests/test_selection.py; cli.py integration in Task 4.
**Interfaces:** parse_size(str) -> int; filter_files(files, *, selected=None, includes=(), excludes=(), extensions=(), min_size=None, max_size=None, largest=False) -> set[int].
- [x] Add and run tests for case-insensitive globs/extensions, inclusive sizes, intersections, empty matches and deterministic largest.
- [x] Implement pure filtering and size parsing; verify with `uv run pytest tests/test_selection.py`.

### Task 2: Cache and resume lifecycle

**Files:** cache.py, engine.py, tests/test_cache.py, tests/test_engine.py, tests/conftest.py.
**Interfaces:** CacheStore metadata/state read/write; MagnetDownload cache options, saved_selection(destination), download(..., recheck=False).
- [x] Add failing tests for offline metadata, resume without payload/rechecking, stale and corrupt state, output isolation and selection changes.
- [x] Store owner-only atomic cache files. Validate hash, schema and file stats before reusing snapshots; override paths, flags, priorities and network sources on restore.
- [x] Replace metadata handle with a final-path download handle; use native resume when valid, native checking otherwise. Save every 30 seconds and before removing the torrent on exit.
- [x] Verify local v1/v2/hybrid downloads and cancellation with `uv run pytest tests/test_cache.py tests/test_engine.py`.

### Task 3: Diagnostics and availability

**Files:** diagnostics.py, engine.py, tests/test_diagnostics.py.
**Interfaces:** DiagnosticSnapshot, per-file coverage; MagnetDownload.diagnostics(selected=None) and probe(seconds, selected=None).
- [x] Add failing tests for boundary pieces, no peers, seeded peers, empty files, redacted tracker errors and no-payload probing.
- [x] Collect tracker replies/errors, DHT routing stats, peer counts, listening failures and current piece availability.
- [x] Verify with `uv run pytest tests/test_diagnostics.py`.

### Task 4: CLI, documentation and review

**Files:** cli.py, README.md, tests/test_cli.py.
- [x] Add failing CLI tests for filtering, no-match errors, saved selection, cache controls and diagnostic-only runs.
- [x] Integrate cache notices, automatic error diagnostics, filter selection and probe display; document examples and cache limitations.
- [x] Run full pytest, Ruff, formatting and build; request read-only independent review and fix meaningful findings.

## Execution notes

- Repository has no initial commit; changes remain reviewable in the working directory instead of creating commits containing the user's initial scaffold.
- User has already authorized implementation; proceed directly without a separate plan-approval round.

## Verification record

- Full suite: `uv run pytest -q` — 88 passed in 69.50 seconds.
- Ruff checks and formatting passed; wheel/source distribution built; lockfile verified.
- Independent native review found and rechecked fixes for tracker URL method calls, cache network-source isolation, and preserved-mtime file replacements. All three regressions were reproduced before fixes; reviewer reran four targeted cases successfully.
- Tests cover local swarms and a local HTTP tracker. Public tracker/DHT reachability depends on the user network and resource availability.
