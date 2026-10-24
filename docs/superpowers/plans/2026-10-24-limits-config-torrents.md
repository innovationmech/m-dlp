# Limits, configuration and torrent files

The user approved all three features. Keep the existing CLI, file selection, cache and resume behavior.

- [x] Add download/upload limits in bytes per second, accepting the existing size units and 0 for unlimited. Apply to local peers too, and replace saved limits on resume.
- [x] Add a flat TOML config with output, limits, timeouts and cache settings. Precedence: explicit CLI > config > defaults. Support --config and --no-config, M_DLP_CONFIG, XDG/platform config locations, strict validation, and file-relative paths. Help/version must work even with broken config.
- [x] Accept local .torrent files through the same positional argument, preserving trackers, web seeds and v2 piece layers. Add metadata-only --export-torrent; preserve identity, never overwrite an existing file, and explain incomplete v2 piece layers where necessary.
- [x] Test real local swarm downloads, config precedence/errors, offline file listing, export round trips, no preselection payload, and changed limits on resume.
- [x] Update README and config.example.toml; run full pytest, Ruff and build. Use superpowers:requesting-code-review for independent review and address findings.

No new runtime dependencies: use Python 3.12 tomllib and the existing libtorrent API. No commits: repository has no initial commit.


## Verification

- Final full suite: `uv run pytest -q` — 127 passed in 152.24 seconds.
- `uv run ruff check .` and `uv run ruff format --check .` passed.
- `uv build` produced wheel and source distribution; `uv lock --check` passed.
- Independent review identified invalid home expansion and NUL config paths. Five regression cases failed before the fix; all 26 option tests passed afterward and the reviewer confirmed the fix.
- Real local swarm tests cover v1/v2/hybrid file input, metadata-only export and subsequent download, limits on local transfers, changed limits on resume, configuration precedence, and no payload before selection.
- The Python binding cannot convert write_torrent_file_buf's native vector return; export uses bencode(write_torrent_file(...)) instead. Missing v2 piece layers use libtorrent's documented metadata-only export flag with a user-visible notice.
