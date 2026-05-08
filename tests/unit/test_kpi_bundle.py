"""Phase 3 Step 1 -- typed KPIBundle dataclass + KPIStatus enum.

Smoke tests for the Phoenix-native KPIBundle that flows through Trinity
Core's pipeline per architecture v1 Section 2.5.
"""

from __future__ import annotations

import phoenix  # noqa: F401  -- triggers sys.path injection for vendored modules


def test_kpi_status_values_are_stable() -> None:
    """The three KPIStatus values are part of the v1 contract."""
    from phoenix.trinity.orchestrate.kpi_bundle import KPIStatus

    assert KPIStatus.OK.value == "ok"
    assert KPIStatus.WARN.value == "warn"
    assert KPIStatus.FAIL.value == "fail"
    assert {s.value for s in KPIStatus} == {"ok", "warn", "fail"}


def test_kpi_bundle_constructs_with_all_six_fields() -> None:
    """Section 2.5 names six fields; the dataclass enforces them."""
    from phoenix.trinity.orchestrate.kpi_bundle import KPIBundle, KPIStatus

    bundle = KPIBundle(
        fidelity=0.99,
        latency_us=120.5,
        backaction=0.01,
        shots_used=1024,
        shot_budget=2048,
        status=KPIStatus.OK,
    )
    assert bundle.fidelity == 0.99
    assert bundle.latency_us == 120.5
    assert bundle.backaction == 0.01
    assert bundle.shots_used == 1024
    assert bundle.shot_budget == 2048
    assert bundle.status is KPIStatus.OK


def test_kpi_bundle_is_frozen() -> None:
    """KPIBundle is immutable -- audit-trail invariant."""
    import dataclasses

    from phoenix.trinity.orchestrate.kpi_bundle import KPIBundle, KPIStatus

    bundle = KPIBundle(
        fidelity=0.95,
        latency_us=100.0,
        backaction=0.05,
        shots_used=512,
        shot_budget=512,
        status=KPIStatus.WARN,
    )
    try:
        bundle.fidelity = 0.42  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("KPIBundle should be frozen but mutation succeeded")
