# phoenix/cli

## Purpose
The `phoenix` CLI entry point. Per architecture v1 Section 5.4, the CLI is a thin wrapper around the REST API — the same canonical contract REST, WebSocket, and MCP all share. Phase 0 ships an empty stub; Phase 9 implements the full command surface.

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 5.4 (CLI surface), Section 5.6 (auth + rate limit + audit shared across protocols), Section 5.8 (CLI latency budget under 8 ms in the separate-daemon path).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `entry.py` | (Phase 9) The `phoenix` command's `main()` — what `pyproject.toml`'s `[project.scripts]` `phoenix = "phoenix.cli.entry:main"` declaration resolves to. |
| `commands/` | (Phase 9) One file per command group: `task`, `lora`, `identity`, `providers`, `audit`, `calibration`, `admin`. |
| `output_formats.py` | (Phase 9) `--output=json|text|table` renderers; defaults by TTY detection. |
| `config_loader.py` | (Phase 9) `~/.phoenix/config.yaml` parser. |

## Vendored substrate
None. `phoenix/cli/` is greenfield Phoenix code.

## Common failure modes
- **`phoenix: command not found` after `pip install -e .`** — Phase 0 declares the entry point but `entry.py` does not yet exist. Invoking the `phoenix` command from a shell fails until Phase 9. Use `python -c "import phoenix; print(phoenix.__version__)"` for Phase 0–8 version checks.

## Troubleshooting
- The pyproject.toml console-script entry resolves to `phoenix.cli.entry:main`, which is a Phase 9 deliverable. The `pip install -e .` install succeeds in Phase 0 but the `phoenix` shell command will not run until Phase 9 lands `entry.py`.
- For tight automation in Phases 0–8, drive Phoenix via `python -m phoenix.api` and direct `httpx`/`curl` calls against the REST API.

## Tests
- `tests/unit/test_smoke.py` — asserts `phoenix.cli` imports.

## Recent changes
- 2026-05-06 — Phase 0: module created as empty stub.
