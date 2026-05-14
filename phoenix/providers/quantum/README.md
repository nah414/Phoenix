# `phoenix/providers/quantum/`

Quantum-provider stubs (IBM Quantum, AWS Braket, IonQ) for the
Router's quantum-eligible routing path. Phase 4 ships
`*_stub.py` files for each provider that satisfy the
`BaseProviderClient` Protocol but raise `NotImplementedError` on
`submit()` -- the routing decisions + cost estimates work end-to-
end; the actual cloud-quantum submit lands when a user has API
credentials wired (v1.x).

The Router treats these stubs as routable candidates in Stage 1's
modality eligibility filter (Section 4); a solve that ends up
dispatched to one fails with `OrchestrateProviderError` carrying
`bundle_hash` + `provider_id` so the user has full context.

Architectural reference: `PHOENIX_ARCHITECTURE_v1.md` Section 4
(routing), Section 4.7 (cost estimation -- per-provider pricing
ladder in `phoenix/router/pricing/pricing_v1.json`).
