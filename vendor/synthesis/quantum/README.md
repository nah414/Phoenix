# `vendor/synthesis/quantum/`

Tensor-network Lindblad solver -- the bridge between the
classical equations layer (`vendor/synthesis/equations/`) and
quantum-circuit-shaped providers. Used by the cross-precision
axis (Section 6.2) when the high-grid solver run requires
tensor-network primitives for Lindblad-form decoherence.

Per Section 11.7.1's vendor-verbatim discipline, kept unchanged
from upstream.

Architectural reference: `PHOENIX_ARCHITECTURE_v1.md` Section 2.3
(Solver engine), Section 6.2 (cross-precision axis).
