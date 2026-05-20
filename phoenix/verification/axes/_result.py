"""Result + provenance dataclasses for cognition wobble axes.

Per Phase 13 build guide Step 4: cognition axes return raw
:class:`CognitionDisagreementMetric` instances with
``disagreement_type = CognitionDisagreementType.UNCLASSIFIED`` when
no classifier is configured.

**Step 5b type swap:** the ``disagreement_type`` field is now typed
:class:`cognition_wobble.disagreement_types.CognitionDisagreementType`
(was :class:`PhoenixDisagreementType` in Step 4). The cognition
substrate owns its native enum end-to-end; the verification gate's
cognition branch (Step 9+) decides the cross-enum integration with
:class:`PhoenixDisagreementType` at that boundary. The earlier
``PhoenixDisagreementType.COGNITION_UNCLASSIFIED`` value remains in
the Phoenix-side enum for future gate-level use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cognition_wobble.disagreement_types import CognitionDisagreementType
from phoenix.providers.cognition.types import CognitionResult, TokenUsage


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
        disagreement_type: A :class:`CognitionDisagreementType` value.
            When the axis ran without a classifier, this is
            ``UNCLASSIFIED``. When a classifier was configured, this
            is the classifier's output class (or ``UNCLASSIFIED`` if
            the classifier's confidence fell below threshold).
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
        classifier_confidence: When a classifier was configured,
            the classifier's reported confidence in
            :attr:`disagreement_type`. ``None`` when no classifier
            was used.
        classifier_version: When a classifier was configured, the
            classifier's version string for ledger provenance.
            ``None`` when no classifier was used.
        metadata: Free-form axis-specific extras (e.g.
            ``"perturbation_prompts"`` for PromptPerturbationAxis).
    """

    axis_name: str
    distance: float
    disagreement_type: CognitionDisagreementType
    pairwise_distance_matrix: list[list[float]]
    provenance: list[CognitionAxisProvenance]
    responses: list[CognitionResult]
    classifier_confidence: float | None = None
    classifier_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
