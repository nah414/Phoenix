# `phoenix/providers/cloud_gpu/`

Cloud GPU provider implementations (Lambda Cloud, RunPod, etc.) for
the Router's GPU-eligible routing path. Phase 4 ships stubs only --
the routing layer recognizes the modality and pricing model but the
concrete client wiring lands when Phoenix's first user-driven GPU
workload arrives in v1.x.

Architectural reference: `PHOENIX_ARCHITECTURE_v1.md` Section 4
(provider registry + routing), Section 4.7 (cost estimation
includes GPU per-hour pricing).
