"""Trinity Core Control subsystem -- engine adapter.

Wraps the vendored :class:`synthesis.core.dpd_engine.DPDScheduler` into
Phoenix's :class:`CandidateAnswer` -> :class:`VerifiedAnswer` flow per
architecture v1 Section 2.4.

Phase 3 scope:

- :func:`_initial_density_matrix` builds a placeholder dim=2 pure-state
  :math:`\\rho = |0\\rangle\\langle 0|` from a :class:`CandidateAnswer`.
  Phase 5 expands to multi-state :math:`\\rho` derived from the full
  eigenstate (the high-grid :class:`SolverRunResult` preserved on the
  Axis-1 :class:`AxisResult`'s metadata is the source). Marked
  ``[OPEN: Phase 5 expansion]`` per the Phase 3 plan's R2 risk.
- :func:`_build_default_dpd_blocks` constructs the canonical DPD block
  sequence for a given probe strength. Phase 3 ships a single
  :class:`DPDBlock` per run (sigma-z probe, weak-measurement Kraus
  operators); Phase 5+ may construct adaptive multi-block sequences once
  the verification gate composer informs block selection.
- :func:`run_dpd` is the top-level public API. Builds blocks, invokes
  ``DPDScheduler(backend=get_backend(hardware_modality)).execute(rho0)``,
  and returns a typed :class:`ControlRunResult`. **SAFETY:** raises
  :class:`ControlVerificationError` when the vendored Lindblad RK4
  propagator self-reports trace drift > 1e-3 or positivity violation; a
  violation here is a real physics error, not a soft warning.

Per architecture Decision 37 (2026-05-06 SynQc-greenfield revision):
Control's ``engine.py`` is a thin Phoenix-side adapter over vendored
substrate. The vendored substrate (``synthesis/core/dpd_engine.py``) does
the physics; this module owns the typed-error discipline and the
PhysicsTask-aware block construction.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from phoenix.trinity.data_model import CandidateAnswer
from phoenix.trinity.orchestrate.kpi_bundle import KPIBundle, KPIStatus

if TYPE_CHECKING:
    from synthesis.core.dpd_engine import DPDBlock, DPDResult


# ---------------------------------------------------------------------------
# Typed errors


class ControlEngineError(Exception):
    """Base for Trinity Core Control engine failures."""


class ControlVerificationError(ControlEngineError):
    """The vendored DPD propagator self-reported a verification violation.

    Raised when:
    - ``abs(dpd_result.trace_preservation - 1.0) > 1e-3`` (trace drift; the
      RK4 Lindblad propagator should preserve trace; meaningful drift means
      the propagator broke or the input :math:`\\rho` was malformed).
    - ``dpd_result.positivity_check is False`` (one or more eigenvalues of
      the post-DPD :math:`\\rho` were below ``-1e-10``; the state is
      unphysical).

    Per architecture Section 7 (fail-closed posture), Phase 3 surfaces these
    as HTTP 422 at the front door (Step 9). Phase 6's safety gate adds a
    pre-execution capability check above this layer.
    """

    def __init__(
        self,
        reason: str,
        *,
        trace_preservation: float,
        positivity_check: bool,
    ) -> None:
        super().__init__(reason)
        self.trace_preservation = trace_preservation
        self.positivity_check = positivity_check


# ---------------------------------------------------------------------------
# Typed result


@dataclass(frozen=True)
class ControlRunResult:
    """One DPD invocation's structured output.

    Phase 3's pipeline orchestrator (Step 8) calls :func:`run_dpd` once for
    the canonical (weak-probe) leg to produce the verified
    :math:`\\rho_{\\text{verified}}`; :class:`CrossControlAxis` (Step 4)
    calls :func:`run_dpd` a second time at the strong-probe strength for the
    Axis-2 disagreement comparison. Both legs return this typed result; the
    cross-probe helper computes trace distance between the two
    ``rho_verified`` arrays.
    """

    rho_verified: np.ndarray
    dpd_result: DPDResult
    wall_clock_ms: float
    probe_strength_used: float
    kpi_bundle_control: KPIBundle


# ---------------------------------------------------------------------------
# Internal helpers


def _initial_density_matrix(candidate: CandidateAnswer, *, dim: int = 2) -> np.ndarray:
    """Construct the pre-DPD density matrix :math:`\\rho_0` from a
    :class:`CandidateAnswer`.

    Phase 3 placeholder: :math:`|0\\rangle\\langle 0|` in ``dim x dim`` --
    a pure ground state with no superposition. The vendored RK4 + Kraus
    propagator handles this input correctly (trace 1, positive
    eigenvalues), exercising the full pipeline without committing to a
    specific physics interpretation of ``CandidateAnswer.value``.

    Phase 5 expansion: when the verification gate composer extends the
    :class:`WobbleAxis` Protocol to plumb the full high-grid
    :class:`SolverRunResult` through to Control, this function will derive
    :math:`\\rho_0` from the eigenstate matching ``CandidateAnswer.value``
    (the ground-state eigenvector of the dispatched Hamiltonian). The
    ``candidate`` parameter is accepted now so the Phase 5 surface lands
    without changing call sites.
    """
    del candidate  # explicit acknowledgement: unused at v1 (Phase 5 wires)
    rho = np.zeros((dim, dim), dtype=complex)
    rho[0, 0] = 1.0 + 0.0j
    return rho


def _build_default_dpd_blocks(
    candidate: CandidateAnswer,
    *,
    probe_strength: float,
    hardware_modality: str = "superconducting",
) -> list[DPDBlock]:
    """Construct the Phase 3 default DPD block sequence.

    Phase 3 ships a single weak-measurement block with sigma-z probe at the
    requested ``probe_strength``. The drive Hamiltonians default to the
    2x2 identity (no Hamiltonian evolution during D1/D2; the block is
    effectively a probe-only operation) -- this isolates the cross-control
    wobble signal to the Kraus-induced backaction at the two probe
    strengths, which is what Axis 2's trace-distance metric measures.

    The vendored :class:`DPDBlock` ``__post_init__`` defaults
    ``probe_observable`` to sigma-z and copies ``drive2_*`` from
    ``drive1_*`` when omitted; Phase 3 accepts those defaults.

    Future expansion (Phase 5+):
    - Adaptive D2 corrections from drift prediction (the vendored
      :class:`DPDScheduler` already supports this via ``adaptive=True`` on
      blocks; Phase 3 stays non-adaptive for reproducibility).
    - Multi-block sequences keyed off the dispatched Hamiltonian regime.

    The ``candidate`` and ``hardware_modality`` parameters are accepted
    for forward-compat with Phase 5+'s regime-aware block construction;
    Phase 3 ignores them but the signature is stable.
    """
    del candidate, hardware_modality  # accepted for Phase 5 forward-compat
    from synthesis.core.dpd_engine import DPDBlock, ProbeType

    block = DPDBlock(
        drive1_hamiltonian=np.eye(2, dtype=complex),
        drive1_duration=10e-9,
        probe_type=ProbeType.WEAK_MEASUREMENT,
        probe_strength=probe_strength,
        probe_duration=1e-10,
        adaptive=False,
        label=f"phase_3_default_eps_{probe_strength:.2f}",
    )
    return [block]


def _classify_kpi_status(fidelity: float) -> KPIStatus:
    """Phase 3 fidelity-based classification.

    Conservative defaults; Phase 5's verification gate may override based on
    cross-axis disagreement. Marked ``[OPEN: Phase 5 thresholds]`` in the
    plan's risk register.
    """
    if fidelity >= 0.95:
        return KPIStatus.OK
    if fidelity >= 0.5:
        return KPIStatus.WARN
    return KPIStatus.FAIL


# ---------------------------------------------------------------------------
# Public engine API


def run_dpd(
    candidate: CandidateAnswer,
    *,
    probe_strength: float = 0.1,
    hardware_modality: str = "superconducting",
) -> ControlRunResult:
    """Run the vendored DPDScheduler at the specified probe strength.

    Phase 3's pipeline orchestrator (Step 8) calls this once with the
    canonical ``probe_strength=0.1`` (weak); :class:`CrossControlAxis`
    (Step 4) calls it again with ``probe_strength=0.5`` (strong) for the
    Axis-2 disagreement leg.

    SAFETY: raises :class:`ControlVerificationError` if the vendored
    propagator self-reports trace drift > 1e-3 or positivity violation.
    Both conditions are surfaced as HTTP 422 at the front door (Step 9).

    Returns :class:`ControlRunResult` carrying the post-DPD
    :math:`\\rho_{\\text{verified}}`, the raw :class:`DPDResult`, wall-clock
    timing, and a populated :class:`KPIBundle` derived from the run's
    backaction.
    """
    # Lazy imports to keep module load fast on fresh clones (vendor/ may not
    # be populated until Phase 1's vendor sync runs) and to avoid the
    # circular concern with the TYPE_CHECKING block above.
    from synthesis.core.dpd_engine import DPDScheduler
    from synthesis.core.hardware_backends import get_backend

    rho0 = _initial_density_matrix(candidate)
    blocks = _build_default_dpd_blocks(
        candidate,
        probe_strength=probe_strength,
        hardware_modality=hardware_modality,
    )

    backend = get_backend(hardware_modality)
    scheduler = DPDScheduler(
        backend=backend,
        inter_block_latency=0.0,
        use_drift_prediction=False,  # Phase 3 stays non-adaptive
    )
    for block in blocks:
        scheduler.add_block(block)

    t_start = time.perf_counter()
    dpd_result = scheduler.execute(rho0)
    wall_clock_ms = (time.perf_counter() - t_start) * 1000.0

    # SAFETY checks -- fail-closed per architecture Section 7.
    if abs(dpd_result.trace_preservation - 1.0) > 1e-3:
        raise ControlVerificationError(
            (
                f"DPD trace preservation drift exceeds 1e-3: "
                f"trace_preservation={dpd_result.trace_preservation:.6f}. "
                f"The vendored Lindblad RK4 propagator should preserve trace; "
                f"meaningful drift indicates a propagator failure or malformed "
                f"input rho. Refusing to return an unphysical VerifiedAnswer."
            ),
            trace_preservation=dpd_result.trace_preservation,
            positivity_check=dpd_result.positivity_check,
        )
    if not dpd_result.positivity_check:
        raise ControlVerificationError(
            (
                "DPD positivity check failed: post-DPD rho has eigenvalues below "
                "-1e-10 (unphysical state). Refusing to return an unphysical "
                "VerifiedAnswer."
            ),
            trace_preservation=dpd_result.trace_preservation,
            positivity_check=dpd_result.positivity_check,
        )

    # Phase 3 KPI bundle: fidelity from inverse-backaction; latency reported
    # in the unit DPDResult uses (seconds) converted to microseconds.
    fidelity = float(np.clip(1.0 - dpd_result.total_backaction, 0.0, 1.0))
    latency_us = float(dpd_result.timing.get("wall_seconds", 0.0)) * 1e6
    kpi_bundle = KPIBundle(
        fidelity=fidelity,
        latency_us=latency_us,
        backaction=float(dpd_result.total_backaction),
        shots_used=1,  # Phase 3 single-execution; cloud providers (Phase 4) report real shots
        shot_budget=1,
        status=_classify_kpi_status(fidelity),
    )

    return ControlRunResult(
        rho_verified=dpd_result.final_state,
        dpd_result=dpd_result,
        wall_clock_ms=wall_clock_ms,
        probe_strength_used=probe_strength,
        kpi_bundle_control=kpi_bundle,
    )
