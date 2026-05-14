# Phoenix v1 reproducibility

Phoenix's reproducibility surface is one of the load-bearing
architectural guarantees: a `Result` envelope's `task_id` can be
replayed against a later Phoenix release and the output is bit-exact
when run in the right mode.

The guarantee is **strong but bounded**. This doc names the asterisks
explicitly so consumers don't build assumptions Phoenix doesn't make.

## Reproducibility modes

Phoenix exposes three modes on `POST /v1/tasks/{id}/replay`:

| Mode | Guarantee |
|---|---|
| `permissive` | Replay best-effort; pipeline reruns from the recorded task spec. Cloud quantum providers may return new shots; results may differ within statistical noise. |
| `strict` | Replay reads the recorded `cloud_shots` from the ledger when the original run used a cloud provider. Post-shot pipeline matches bit-exactly. |
| `replay` | Strictest mode. Replay refuses to start if any required ledger field is missing; bit-exact match across solver + control + orchestrate verified at the gate. |

## The cloud-shots-recorded asterisk

Per Section 1 Decision 20, the architecture accepts that **cloud-quantum
shots are intrinsically nondeterministic** -- a single circuit, run
twice on the same IBM / Braket / IonQ backend, returns different
samples each time. This is a property of quantum measurement, not a
Phoenix bug.

Phoenix's response: on a cloud-quantum solve, **the shots themselves
are recorded in the Omega Ledger** alongside the task envelope. On
strict / replay mode, the recorded shots are replayed from the ledger
rather than re-requested from the provider. The post-shot pipeline --
Trinity Core's solver + control + orchestrate -- runs deterministically
against the replayed shots, so the `Result.value` reproduces bit-exactly.

**The asterisk:** the strongest reproducibility guarantee Phoenix can
make for a cloud-quantum solve is "the post-shot pipeline reproduces
bit-exactly; the original cloud run cannot." If you replay a cloud
solve in `permissive` mode, you'll get a different `Result.value`
within statistical bounds -- this is correct behavior, not a regression.

## The `cloud_shots_recorded` provenance field

Every `Result` envelope carries a `cloud_shots_recorded: bool` in its
provenance block per Section 11 RESOLVED disposition. Consumers can
read this field to decide whether bit-exact replay is achievable:

```python
result = client.post("/v1/tasks", json=task_spec).json()
if result["provenance"]["cloud_shots_recorded"]:
    # This solve ran a cloud-quantum provider; the ledger has the
    # raw shots, and strict/replay mode will reproduce bit-exactly.
    pass
else:
    # This solve ran classical-only (local simulator, IBM noiseless
    # simulator, etc.). Strict/replay always reproduces bit-exactly.
    pass
```

The field is `false` for local-simulator runs and `true` for any
solve that hit a cloud-quantum provider. Phoenix never has to
"degrade reproducibility silently" -- the field is the consumer's
explicit hint about which guarantee applies.

## What reproducibility does NOT cover

To name the limits explicitly:

- **Cloud provider's internal calibration drift.** If IBM
  recalibrates a backend's basis gates between your original run and
  your replay, the recorded shots are still valid (they captured the
  state at original-run time) -- but a fresh run after recalibration
  would produce different shots. Phoenix does NOT recalibrate the
  shots against the new basis; that would defeat the bit-exact
  guarantee.
- **System clock skew across replay boundaries.** Phoenix's hashchain
  is independent of wall-clock; the bit-exact replay test in Section
  10.7 simulates a 180-day clock skew between record and replay, and
  the replay still matches bit-exactly.
- **Hardware-level numerics across CPUs.** Phoenix's Tier-1 + solver
  numerics are double-precision IEEE-754 throughout. Cross-architecture
  bit-exact reproducibility (x86-64 vs ARM64) is **tested but not
  guaranteed**; some intrinsic operations (e.g. SIMD FMA fusion) can
  produce sub-ULP differences that compose into observable
  differences after many iterations. This is a Phase 12.5 / v1.1
  area of investigation.

## The acceptance contract

Section 10.7's long-window-replay acceptance test pins this whole
contract. The test:

1. Hand-builds a `SolveEntry` fixture for a known QHO solve.
2. Records it through the Omega Ledger.
3. Monkey-patches the system clock to advance 180 days.
4. Calls `pipeline.replay(task_id, mode="replay")`.
5. Asserts `Result.value` matches the recorded value **bit-exactly**.

This test is `@pytest.mark.acceptance`-gated and runs in CI. A
regression here means Phoenix's reproducibility guarantee has
silently weakened -- the test is intentionally aggressive about
catching that.
