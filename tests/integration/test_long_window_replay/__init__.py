"""Phase 11 long-window replay test suite (architecture v1 Section 10.7).

The §10.7 "long-window replay test" acceptance gate ships as
:mod:`test_six_month_replay` in this directory (Step 7).
Step 6 ships the substrate: clock-monkeypatch helper +
deterministic SolveEntry fixture builder.

Per locked OPEN-2 (Phase 11): the test monkeypatches
``time.time()`` to fast-forward 6+ simulated months rather than
waiting wall-clock. Per locked OPEN-3: the SolveEntry fixture is
hand-built (deterministic, self-contained) rather than depending
on a captured production solve.

All tests carry ``@pytest.mark.acceptance`` so the v1 gate runs
as ``pytest -m acceptance``.
"""
