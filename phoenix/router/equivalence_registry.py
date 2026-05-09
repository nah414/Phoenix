"""Provider equivalence registry for failover decisions.

Per architecture v1 Section 4.5: when the Router fails over from a primary
provider to alternates, it needs an equivalence rule that says "circuit X
on provider A is acceptable as a substitute for circuit X on provider B."
Section 11.2.1 [OPEN] notes general equivalence is a research problem;
v1 ships **conservative defaults** plus a manual override interface.

**Phase 4 conservative defaults:**

Two providers are equivalent when ALL of:

1. ``quantum_technology`` matches (e.g. both ``"superconducting"``).
2. Same qubit count (Phase 4 placeholder: assumes single-qubit-class
   matching since the vendored ``HardwareParams.max_qubits`` distinguishes
   coarse capacity tiers).
3. Estimated fidelity within 10% of the original. The intelligence layer
   (Step 5) provides the fidelity estimate.

**v1.x extension:** ops can register custom rules via JSON config or the
dev-ops backdoor (Phase 8 endpoint) that override these defaults. Phase 4
ships the API surface for overrides; the JSON-backed config + admin
endpoint land later.

The Router's failover protocol (Step 8) consults this registry when
walking :attr:`RoutingDecision.alternates`. Alternates the Router itself
constructed (Stage 6 ranking output) are equivalence-checked at
construction time; runtime failover uses this module to filter the
already-ranked alternates list.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phoenix.router.provider_registry import ProviderEntry

log = logging.getLogger(__name__)

# Phase 4 conservative fidelity tolerance: alternates must report
# estimated fidelity within 10% of the original.
_DEFAULT_FIDELITY_TOLERANCE = 0.10


def is_equivalent(
    primary: ProviderEntry,
    alternate: ProviderEntry,
    *,
    primary_fidelity: float,
    alternate_fidelity: float,
    fidelity_tolerance: float = _DEFAULT_FIDELITY_TOLERANCE,
) -> bool:
    """Phase 4 conservative equivalence check.

    Returns ``True`` when ``alternate`` is an acceptable substitute for
    ``primary`` for failover purposes:

    - Same ``quantum_technology``.
    - Estimated fidelity within ``fidelity_tolerance`` of the original.

    Phase 4 does NOT check qubit count explicitly; the vendored
    :class:`HardwareParams.max_qubits` distinguishes coarse tiers but Phase
    4 assumes single-qubit-class matching. Phase 5+ may add a qubit-class
    check when the verification gate composes Axis 3 (cross-provider).

    The ``fidelity_tolerance`` parameter is exposed so ops can tune via
    Phase 8's admin endpoint without code changes; Phase 4 default is
    Section 4.5's 10%.
    """
    if primary.client.quantum_technology != alternate.client.quantum_technology:
        return False
    # Fidelity tolerance check: alternate.fidelity >= primary.fidelity * (1 - tolerance).
    # I.e. alternate must be no worse than 10% below primary's fidelity.
    threshold = primary_fidelity * (1.0 - fidelity_tolerance)
    if alternate_fidelity < threshold:
        return False
    return True


def filter_equivalent_alternates(
    primary: ProviderEntry,
    alternates: list[ProviderEntry],
    *,
    primary_fidelity: float,
    alternate_fidelities: dict[str, float],
    fidelity_tolerance: float = _DEFAULT_FIDELITY_TOLERANCE,
) -> list[ProviderEntry]:
    """Filter ``alternates`` down to those equivalent to ``primary``.

    Used by the Router's failover protocol to drop non-equivalent
    candidates before retrying. ``alternate_fidelities`` is a dict
    ``{provider_id: estimated_fidelity}`` -- typically the intelligence
    layer's :func:`estimate_fidelity` output computed once at the
    :class:`RoutingDecision` construction site.
    """
    equivalent: list[ProviderEntry] = []
    for alt in alternates:
        alt_fid = alternate_fidelities.get(alt.provider_id, 0.0)
        if is_equivalent(
            primary,
            alt,
            primary_fidelity=primary_fidelity,
            alternate_fidelity=alt_fid,
            fidelity_tolerance=fidelity_tolerance,
        ):
            equivalent.append(alt)
        else:
            log.debug(
                "filter_equivalent_alternates: dropping %s (tech=%s, fidelity=%.4f) "
                "as non-equivalent to %s (tech=%s, fidelity=%.4f, threshold=%.4f)",
                alt.provider_id,
                alt.client.quantum_technology,
                alt_fid,
                primary.provider_id,
                primary.client.quantum_technology,
                primary_fidelity,
                primary_fidelity * (1.0 - fidelity_tolerance),
            )
    return equivalent
