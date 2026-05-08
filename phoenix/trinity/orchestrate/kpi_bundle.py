"""Typed :class:`KPIBundle` dataclass for Trinity Core's Orchestrate subsystem.

Per architecture v1 Section 2.5: every Phoenix solve produces a typed KPI
bundle that flows through the pipeline alongside the density-matrix data and
provenance trace. Phase 3 introduces the typed dataclass; before this Phase
the bundle was carried as ``dict[str, Any]`` on :class:`VerifiedAnswer` and
:class:`Result` for forward-compat. Phase 3 tightens those field types as
part of Step 2.

Section 2.5 names six fields plus a ``KPIStatus`` enum. The enum's three
values are stable across releases; new statuses (e.g. ``DEGRADED``) append.
The Phoenix-native :class:`KPIBundle` is **not vendored** — Section 2.5's
Decision 37 commits Orchestrate to greenfield code organized around
Phoenix's task lifecycle, not SynQc TDS Core's terminology. This module
houses only the dataclass; consumers (Control, Orchestrate, the verification
gate at Phase 5+) import from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class KPIStatus(Enum):
    """Coarse health classification on each :class:`KPIBundle`.

    Phase 3 ships the three values architecture Section 2.5 names. The
    classification is set by Orchestrate's :func:`extract` (per Phase 3 Step
    5) using fidelity-based thresholds; Phase 5's verification gate may
    override based on cross-axis disagreement.
    """

    OK = "ok"
    """Solve completed within tolerance; KPIs nominal."""

    WARN = "warn"
    """Solve completed but a KPI breached a soft threshold (e.g. fidelity
    below ~0.95 in Phase 3's local-simulator path). The :class:`Result` is
    still usable; ops should investigate. Not a fail-closed condition."""

    FAIL = "fail"
    """KPI bundle is outside acceptable bounds. Phase 3 surfaces this
    advisorily; Phase 5's gate will refuse to return a :class:`Result` with
    this status without an explicit acknowledge from the caller."""


@dataclass(frozen=True)
class KPIBundle:
    """Phoenix-native KPI aggregate per architecture v1 Section 2.5.

    Phase 3 wires this through Control's :class:`VerifiedAnswer`
    (``kpi_bundle_control``) and Orchestrate's :class:`Result`
    (``kpi_bundle_orchestrate``). Phase 5's verification gate can compose
    multiple bundles when cross-axis verification fires; Phase 3 ships one
    bundle per layer.

    Fields:
        fidelity: Estimated state fidelity in :math:`[0, 1]`. Phase 3's
            local-simulator path computes ``1.0 - total_backaction`` clamped
            to the unit interval; Phase 4's cloud adapters extract this from
            provider-reported fidelity metadata.
        latency_us: Wall-clock latency of the provider's submission +
            execution in microseconds. Excludes Phoenix-side translation
            overhead (which is negligible for the local-simulator path).
        backaction: DPD-induced backaction magnitude (dimensionless;
            Section 2.4). Sourced from
            :attr:`DPDResult.total_backaction` for the canonical run.
        shots_used: Actual shot count consumed by the provider. For the
            local-simulator path this matches the requested shot budget;
            cloud providers may report differently (queue truncation, etc.).
        shot_budget: Originally requested shot budget. Phase 3 sets this
            equal to ``shots_used`` for the local-simulator path; Phase 4's
            Router computes a real budget from the routing decision.
        status: :class:`KPIStatus` health classification. See enum docstring.
    """

    fidelity: float
    latency_us: float
    backaction: float
    shots_used: int
    shot_budget: int
    status: KPIStatus
