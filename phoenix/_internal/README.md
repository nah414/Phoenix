# phoenix/_internal

## Purpose
**Internal utilities** plus the **Phoenix Cloud abstraction seams** per architecture v1 Section 10.3.1. Phase 0 ships the version constant + vendor-manifest reader. Later phases add the structured logger, config-file parser, root exception hierarchy, and the three Phoenix Cloud abstraction-seam Protocols (`HttpAuthExtractor`, `AuditLogExporter`, `JobBudgetController`).

## Architectural reference
PHOENIX_ARCHITECTURE_v1.md Section 10.3 (utilities file layout), Section 10.3.1 (Phoenix Cloud abstraction seams — concrete Protocol spec), Section 1 Decision 35 (free + paid hosted SaaS; the seams enable Phoenix Cloud above an unmodified Phoenix-the-middleware).

## Key files and their roles
| File | Role |
|---|---|
| `__init__.py` | Empty Phase 0 stub. |
| `version.py` | (Phase 0 — landed) `__version__ = "1.0.0.dev0"` and `read_vendor_version()`. |
| `logging.py` | (Phase 6) Phoenix's structured logger — typed dataclass events, JSON-serializable. |
| `config.py` | (Phase 6) `~/.phoenix/config.yaml` parser plus env-var override layer. |
| `errors.py` | (Phase 5) Root exception hierarchy. All typed exceptions from Sections 3.7, 4.x, 6.8, 7.8, 8.7 derive from a common base. |
| `cloud_seams.py` | (Phase 10) Three `Protocol` definitions for Phoenix Cloud — `HttpAuthExtractor`, `AuditLogExporter`, `JobBudgetController` — plus default local implementations and a `CloudSeams` registry with `replace_*()` methods. Phoenix code reaches the seams through `phoenix._internal.cloud_seams.get()` — never by direct import of the default impls. |

## Vendored substrate
None. `phoenix/_internal/` is greenfield Phoenix code.

## Common failure modes
None yet — Phase 0 ships only `version.py`; later phases add the surface that can fail.

## Troubleshooting
- `read_vendor_version()` returns `None` if `vendor/VENDOR_VERSION.txt` doesn't exist (true before Phase 0 Step 3) and the parsed dict otherwise. Phase 1 populates real hashes.
- Cloud seams are deliberately kept narrow — a Phoenix Cloud impl can only return Actors that pass the safety gate's HMAC verification, write audit events, and decide budget allow/deny. It cannot suppress safety-gate denials or inject privileged Actors.

## Tests
- `tests/unit/test_smoke.py` (Phase 0) — asserts `phoenix._internal.version.__version__` and `read_vendor_version()` shape.
- `tests/integration/test_cloud_seams.py` (Phase 10) — swaps mock Phoenix-Cloud-shaped impls and verifies all three seams compose without modifying Phoenix code, per Section 10.3.1's acceptance criterion.

## Recent changes
- 2026-05-06 — Phase 0: `version.py` + `read_vendor_version()` landed (Step 2). Module README created (Step 5).
