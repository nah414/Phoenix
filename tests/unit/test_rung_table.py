"""Phase 5 Step 1 -- rung_table boundary + walking semantics."""

from __future__ import annotations

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules


def test_select_initial_rung_boundaries() -> None:
    """Section 6.4 indicative thresholds map correctly."""
    from phoenix.verification.rung_table import select_initial_rung
    from phoenix.verification.wobble_axis import RungDepth

    assert select_initial_rung(0.1) is RungDepth.R1_FLOOR
    assert select_initial_rung(1e-2) is RungDepth.R2_CROSS_PRECISION
    assert select_initial_rung(5e-3) is RungDepth.R2_CROSS_PRECISION
    assert select_initial_rung(1e-3) is RungDepth.R3_TWO_AXES
    assert select_initial_rung(5e-4) is RungDepth.R3_TWO_AXES
    assert select_initial_rung(1e-4) is RungDepth.R4_THREE_AXES
    assert select_initial_rung(1e-5) is RungDepth.R4_THREE_AXES
    assert select_initial_rung(1e-6) is RungDepth.R5_REPLICATED
    assert select_initial_rung(1e-9) is RungDepth.R5_REPLICATED


def test_next_rung_walks_up() -> None:
    from phoenix.verification.rung_table import next_rung
    from phoenix.verification.wobble_axis import RungDepth

    assert next_rung(RungDepth.R1_FLOOR) is RungDepth.R2_CROSS_PRECISION
    assert next_rung(RungDepth.R2_CROSS_PRECISION) is RungDepth.R3_TWO_AXES
    assert next_rung(RungDepth.R3_TWO_AXES) is RungDepth.R4_THREE_AXES
    assert next_rung(RungDepth.R4_THREE_AXES) is RungDepth.R5_REPLICATED
    assert next_rung(RungDepth.R5_REPLICATED) is None


def test_previous_rung_walks_down() -> None:
    from phoenix.verification.rung_table import previous_rung
    from phoenix.verification.wobble_axis import RungDepth

    assert previous_rung(RungDepth.R1_FLOOR) is None
    assert previous_rung(RungDepth.R2_CROSS_PRECISION) is RungDepth.R1_FLOOR
    assert previous_rung(RungDepth.R3_TWO_AXES) is RungDepth.R2_CROSS_PRECISION


def test_cost_multiplier_promotion() -> None:
    """Section 6.4 multipliers: R3=3x baseline, R4=5.5x, R5=10x."""
    from phoenix.verification.rung_table import (
        RUNG_WALL_CLOCK_MULTIPLIER,
        estimate_additional_cost_multiplier,
    )
    from phoenix.verification.wobble_axis import RungDepth

    assert RUNG_WALL_CLOCK_MULTIPLIER[RungDepth.R3_TWO_AXES] == 3.0
    assert RUNG_WALL_CLOCK_MULTIPLIER[RungDepth.R4_THREE_AXES] == 5.5
    # Promoting R3 -> R4: 5.5 / 3.0 = ~1.83
    ratio = estimate_additional_cost_multiplier(RungDepth.R3_TWO_AXES, RungDepth.R4_THREE_AXES)
    assert abs(ratio - (5.5 / 3.0)) < 1e-9
    # Defensive: descending request returns 1.0.
    no_promotion_ratio = estimate_additional_cost_multiplier(
        RungDepth.R4_THREE_AXES, RungDepth.R2_CROSS_PRECISION
    )
    assert no_promotion_ratio == 1.0
