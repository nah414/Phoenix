"""Step 2's ProviderEntry widening was reverted at Step 6.

Background: Step 2 widened :class:`ProviderEntry.client` from
:class:`BaseProviderClient` to the union with
:class:`CognitionProvider`, plus a :meth:`ProviderRegistry.cognition_entries`
filter helper. The widening lacked narrow-on-use guards at the
existing physics-only call sites in
``phoenix/router/{decision,intelligence,equivalence_registry}.py``;
the full ``mypy --strict phoenix/`` gate surfaced 11 union-attr
errors at Step 6's pre-commit hook.

Resolution: revert the widening. Step 4's cognition axes take
providers as constructor args directly, so no Phase 13 code actually
consumed the registry's cognition view. When Step 9 needs a
cognition-provider lookup surface (for the gate's
``task.kind == "cognition"`` branch), the right shape is a separate
``CognitionProviderRegistry`` rather than overloading the physics
:class:`ProviderRegistry` — the data shapes are different
(physics: provider_id + backend_name + quantum_technology;
cognition: provider_id + model + capabilities()).

This module stays as a placeholder documenting the reversal, so the
git history carries the rationale. The test removed: the original
``test_cognition_provider_registers`` would have asserted the union
type works, which is no longer the architectural intent.
"""

from __future__ import annotations


def test_reversal_is_documented() -> None:
    """Placeholder test — see module docstring for the reversal rationale."""
    # The actual assertion: ProviderEntry.client is annotated as
    # BaseProviderClient, NOT the union with CognitionProvider.
    from dataclasses import fields

    from phoenix.router.provider_registry import ProviderEntry

    client_field = next(f for f in fields(ProviderEntry) if f.name == "client")
    # The type annotation is stringified under `from __future__ import annotations`;
    # we just check it doesn't mention CognitionProvider.
    assert "CognitionProvider" not in str(client_field.type)
