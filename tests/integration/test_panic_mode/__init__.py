"""Phase 11 panic-mode test suite (architecture v1 Section 10.7).

The §10.7 "compositional fail-closed test" acceptance gate ships
as four tests in this directory:

- ``test_nats_down.py`` -- QueueUnavailable surfaces on NATS loss.
- ``test_state_backend_down.py`` -- StateBackendUnavailable on
  state-backend loss.
- ``test_drift_detector_down.py`` -- DriftStateUnavailable on
  drift telemetry loss.
- ``test_simultaneous_three.py`` -- all three together fail-close
  with a deterministic typed error naming the FIRST failing
  condition, not a confused mixture.

All four are marked ``@pytest.mark.acceptance`` so the v1 gate
runs as ``pytest -m acceptance``.
"""
