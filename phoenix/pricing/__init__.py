"""Phoenix pricing subsystem.

Phase 13 (v1.1) introduces ``phoenix.pricing.v2`` for cognition (LLM)
pricing alongside the existing ``phoenix.router.pricing`` (physics
pricing). The split reflects different consumer surfaces: physics
pricing feeds the router's cost-ceiling Stage 2 + Stage 6 ranking;
cognition pricing feeds the Phase 10 cost-ceiling engine and the
Phase 13 streaming-token cost-tracking.

Pricing v1 (``phoenix.router.pricing``) remains unchanged.
"""
