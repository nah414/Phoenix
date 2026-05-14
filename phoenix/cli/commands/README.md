# `phoenix/cli/commands/`

Phase 9 Steps 7 + 8 CLI command groups. Each module exports a
`HANDLERS` dict mapping subcommand name → handler function; the
top-level `phoenix.cli.entry` dispatcher routes `argv` to the
appropriate handler.

| Module | Group | Subcommands |
|---|---|---|
| `task.py` | `phoenix task ...` | `submit` / `get` / `replay` / `stream` |
| `lora.py` | `phoenix lora ...` | `load` / `list` / `unload` |
| `identity.py` | `phoenix identity ...` | `show` / `enroll` |
| `providers.py` | `phoenix providers ...` | `list` |
| `audit.py` | `phoenix audit ...` | `tail` / `verify` |
| `calibration.py` | `phoenix calibration ...` | `status` / `run` |
| `admin.py` | `phoenix admin ...` | `kill-switch` / `health` / `governor` / `budget` / `override` |
| `mcp_server.py` | `phoenix mcp ...` | `serve` (boots stdio MCP server, Phase 9 Step 9) |
| `_shared.py` | — | Shared helpers (spec JSON parsing, payload rendering, per-user task cache for `phoenix task get`). |

Each handler signature is:

```python
def handler(args, config, client, output_format) -> int
```

It returns the process exit code (`phoenix.cli.entry.EXIT_OK` etc.).

Architectural reference: `PHOENIX_ARCHITECTURE_v1.md` Section 5.4
(CLI surface), Section 5.5 (MCP surface).
