"""Verification gate's five-rung table (Section 6.4).

Maps the user's :attr:`ToleranceSpec.max_error_bar` to an initial
:class:`RungDepth`, plus exposes the wall-clock multiplier per rung for
the gate's cost-ceiling check before promotion (Section 4.7 Stage 5).

Per architecture v1 Section 6.4 the rung table is fixed for v1; per-install
customization defers to v1.x (Section 11.3.2 OPEN). Per the locked Phase 5
scope decision (2026-05-08): static + reactive promotion. Initial rung
selection comes from this table; mid-pipeline reactive promotion logic
lives in :mod:`phoenix.verification.promotion` (Step 6).

Indicative thresholds per Section 6.4:

============= =========================== ============================
Rung          Indicative max_error_bar    Wall-clock multiplier vs R1
============= =========================== ============================
R1_FLOOR      > 1e-2                      1x
R2_CROSS_P    1e-3 to 1e-2                ~1.7x
R3_TWO_AXES   1e-4 to 1e-3                ~3x
R4_THREE      1e-6 to 1e-4                ~5-6x
R5_REPLIC     <= 1e-6                     ~10x
============= =========================== ============================

Section 11.1.3 OPEN: adaptive threshold tuning deferred to v1.1 once
empirical Tier-1 data exists.
"""

from __future__ import annotations

from phoenix.verification.wobble_axis import RungDepth

# Wall-clock multipliers per rung relative to R1 (Section 6.4).
# Used by the promotion check (Step 6) to estimate the additional cost
# of going from R_n to R_n+1.
RUNG_WALL_CLOCK_MULTIPLIER: dict[RungDepth, float] = {
    RungDepth.R1_FLOOR: 1.0,
    RungDepth.R2_CROSS_PRECISION: 1.7,
    RungDepth.R3_TWO_AXES: 3.0,
    RungDepth.R4_THREE_AXES: 5.5,
    RungDepth.R5_REPLICATED: 10.0,
}


def select_initial_rung(max_error_bar: float) -> RungDepth:
    """Map ``max_error_bar`` to the initial :class:`RungDepth` per Section 6.4.

    Inclusive upper bound on each tier per the spec's "indicative" prose.
    Phase 5 ships these thresholds verbatim from Section 6.4; v1.1 may
    expose them as config (Section 11.1.3 OPEN).

    Boundary semantics:
    - max_error_bar > 1e-2: R1_FLOOR (loose tolerance; no cross-axis).
    - 1e-3 < max_error_bar <= 1e-2: R2_CROSS_PRECISION (Axis 1 only).
    - 1e-4 < max_error_bar <= 1e-3: R3_TWO_AXES (Axes 1 + 2).
    - 1e-6 < max_error_bar <= 1e-4: R4_THREE_AXES (all three; instrument
      grade default).
    - max_error_bar <= 1e-6: R5_REPLICATED (three axes + replication).
    """
    if max_error_bar > 1e-2:
        return RungDepth.R1_FLOOR
    if max_error_bar > 1e-3:
        return RungDepth.R2_CROSS_PRECISION
    if max_error_bar > 1e-4:
        return RungDepth.R3_TWO_AXES
    if max_error_bar > 1e-6:
        return RungDepth.R4_THREE_AXES
    return RungDepth.R5_REPLICATED


def next_rung(current: RungDepth) -> RungDepth | None:
    """Return the rung above ``current``; ``None`` when already at R5.

    Used by the gate's promotion logic (Step 6) when an axis's measured
    disagreement exceeds half the remaining error budget. Per Section 6.4
    the gate may promote at most twice per task; the count enforcement
    lives in the gate itself, not here.
    """
    rungs_in_order = [
        RungDepth.R1_FLOOR,
        RungDepth.R2_CROSS_PRECISION,
        RungDepth.R3_TWO_AXES,
        RungDepth.R4_THREE_AXES,
        RungDepth.R5_REPLICATED,
    ]
    try:
        i = rungs_in_order.index(current)
    except ValueError:
        return None
    if i + 1 >= len(rungs_in_order):
        return None
    return rungs_in_order[i + 1]


def previous_rung(current: RungDepth) -> RungDepth | None:
    """Return the rung below ``current``; ``None`` when already at R1.

    Used by the gate's demotion logic (Step 6) when cumulative
    disagreement is below 10% of ``max_error_bar``. Per Section 6.4 the
    gate may demote at most once per task and only at R2 / R3 boundaries
    (Axis 1 always runs; you never drop to R1 reactively).
    """
    rungs_in_order = [
        RungDepth.R1_FLOOR,
        RungDepth.R2_CROSS_PRECISION,
        RungDepth.R3_TWO_AXES,
        RungDepth.R4_THREE_AXES,
        RungDepth.R5_REPLICATED,
    ]
    try:
        i = rungs_in_order.index(current)
    except ValueError:
        return None
    if i == 0:
        return None
    return rungs_in_order[i - 1]


def estimate_additional_cost_multiplier(current: RungDepth, target: RungDepth) -> float:
    """Estimate the wall-clock cost ratio of promoting from ``current`` to ``target``.

    Returns the ratio ``multiplier(target) / multiplier(current)``. The
    gate's promotion check uses this to estimate added provider cost
    against the remaining cost ceiling: ``estimated_cost * (ratio - 1)``
    is the marginal cost of the promotion.

    When ``target`` is at or below ``current``, returns 1.0 (no extra
    cost). The gate's promotion path always passes ``target`` strictly
    above ``current``; this defensive branch avoids negative ratios.
    """
    cur_mult = RUNG_WALL_CLOCK_MULTIPLIER.get(current, 1.0)
    tgt_mult = RUNG_WALL_CLOCK_MULTIPLIER.get(target, cur_mult)
    if tgt_mult <= cur_mult:
        return 1.0
    return tgt_mult / cur_mult
