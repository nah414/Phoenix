"""Result + provenance dataclasses for cognition wobble axes.

Per Phase 13 build guide Step 4: cognition axes return raw
:class:`CognitionDisagreementMetric` instances with
``disagreement_type = PhoenixDisagreementType.COGNITION_UNCLASSIFIED``.
The Step 5 classifier replaces ``UNCLASSIFIED`` with a real class.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from phoenix.providers.cognition.types import CognitionResult, TokenUsage
from phoenix.verification.agreement_classifier import PhoenixDisagreementType


@dataclass(frozen=True)
class CognitionAxisProvenance:
    """One-call provenance row for a cognition-axis dispatch.

    Built from a successful :meth:`CognitionProvider.complete` call.
    The verification gate (Step 9+) stitches these into the Omega
    Ledger entry alongside the physics-axis provenance.

    Fields:
        provider_id: The provider's stable identifier.
        model: The provider-specific model identifier.
        latency_ms: Wall-clock latency of this provider call.
        usage: Token-level usage record for cost accounting.
        temperature: The temperature used for this call (varies in
            self-consistency axis; constant across cross-model dispatch).
        provider_fingerprint: Echoed from
            :attr:`CognitionResult.provider_fingerprint` for ledger join.
    """

    provider_id: str
    model: str
    latency_ms: float
    usage: TokenUsage
    temperature: float
    provider_fingerprint: str


@dataclass(frozen=True)
class CognitionDisagreementMetric:
    """One cognition axis's structured output.

    Parallels :class:`phoenix.verification.wobble_axis.AxisResult` for
    physics-axis results but with cognition-specific provenance fields
    (per-provider call info instead of grid/probe metadata).

    Fields:
        axis_name: Stable axis identifier (e.g.
            ``"cognition_cross_model"``).
        distance: Aggregate disagreement scalar in [0, 1]. For
            multi-response axes, this is the mean of the off-diagonal
            entries of :attr:`pairwise_distance_matrix`.
        disagreement_type: Per Step 4 spec, always
            :attr:`PhoenixDisagreementType.COGNITION_UNCLASSIFIED`. The
            Step 5 classifier replaces this with a real class.
        pairwise_distance_matrix: N×N matrix where ``[i][j]`` is the
            distance between response ``i`` and response ``j``.
            Diagonal entries are ``0.0``; the matrix is symmetric.
            Preserved per architecture Section 6.2's
            ``DO NOT COLLAPSE`` invariant.
        provenance: One :class:`CognitionAxisProvenance` per dispatched
            call.
        responses: The :class:`CognitionResult` instances produced by
            the axis's dispatches. The Omega Ledger entry references
            these by hash in Step 8+; Step 4 carries the objects
            directly for in-process consumers.
        metadata: Free-form axis-specific extras (e.g.
            ``"perturbation_prompts"`` for PromptPerturbationAxis).
    """

    axis_name: str
    distance: float
    disagreement_type: PhoenixDisagreementType
    pairwise_distance_matrix: list[list[float]]
    provenance: list[CognitionAxisProvenance]
    responses: list[CognitionResult]
    metadata: dict[str, Any] = field(default_factory=dict)
