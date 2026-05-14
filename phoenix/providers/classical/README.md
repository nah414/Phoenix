# `phoenix/providers/classical/`

Classical (non-quantum) provider implementations consumed by the
Router (Section 4). Phase 2's `LocalClassicalSimulator` lives here
as the zero-cost in-process baseline: every Phoenix install can
solve against it without external dependencies, and the long-window
replay test's deterministic fixture (Phase 11 Step 6) uses it
because cloud shots are intrinsically non-deterministic per
Decision 20.

Architectural reference: `PHOENIX_ARCHITECTURE_v1.md` Section 4
(routing layer), Section 1 Decision 20 (cloud-shot reproducibility
asterisk).
