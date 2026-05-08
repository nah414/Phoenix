"""Pure translation layer: :class:`VerifiedAnswer` -> :class:`ProviderSubmission`.

Per architecture v1 Section 2.5: ``bundle_builder`` translates a Phoenix-side
:class:`VerifiedAnswer` (the Control subsystem's typed output) into a
provider-specific submission shape. Pure function; no I/O. Phase 3 ships the
``"classical_density_matrix"`` shape only; Phase 4 fills the
``"qiskit_circuit"``, ``"braket_task"``, and ``"ionq_shot_batch"`` shapes.

The bundle hash (16-char SHA-256 prefix of the canonical-JSON payload) is
computed deterministically per submission so Phase 7's ledger has a stable
fingerprint and Phase 4's Router failover has a cache key.

Greenfield Phoenix code per Decision 37; nothing vendored.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from phoenix.trinity.orchestrate.provider_client import ProviderSubmission

if TYPE_CHECKING:
    from phoenix.router.data_model import ProviderSelection
    from phoenix.trinity.data_model import VerifiedAnswer


def _canonical_json(payload: dict[str, Any]) -> str:
    """Canonical-JSON encoder used for the bundle hash.

    Sort keys, no whitespace, ASCII-safe. Cross-Python-version stable so
    the same payload always hashes to the same prefix; this matters for
    Phase 7's replay protocol.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _bundle_hash(payload: dict[str, Any]) -> str:
    """Stable 16-char SHA-256 prefix of the canonical-JSON payload."""
    canonical = _canonical_json(payload).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def build_submission(
    verified: VerifiedAnswer,
    selection: ProviderSelection,
    *,
    shots: int = 1024,
    request_id: str = "",
) -> ProviderSubmission:
    """Translate a :class:`VerifiedAnswer` into a :class:`ProviderSubmission`.

    Dispatches off ``selection.quantum_technology``:

    - ``"simulation"`` (Phase 3 only): emits a
      ``"classical_density_matrix"`` bundle carrying the post-DPD :math:`\\rho`
      and the DPD block count. The local-simulator adapter consumes this
      shape directly.
    - All other technologies raise :class:`NotImplementedError` with a
      ``Phase 4`` marker. Phase 4 fills the Qiskit/Braket/IonQ shapes.

    Pure translation, no I/O. Returns a :class:`ProviderSubmission` with a
    deterministic ``bundle_hash`` derived from the canonical-JSON of the
    payload.
    """
    tech = selection.quantum_technology

    if tech == "simulation":
        # The local-simulator path doesn't actually re-run physics; the
        # bundle just carries the verified rho so the simulator can produce
        # the trace-expectation as a "fake measurement" outcome (Phase 3
        # placeholder; Phase 5 will wire real observable extraction).
        rho = verified.rho_verified
        payload: dict[str, Any] = {
            "rho": rho.tolist(),
            "rho_dim": int(rho.shape[0]),
            "dpd_n_blocks": int(verified.dpd_result.n_blocks),
        }
        bundle_kind = "classical_density_matrix"
    else:
        raise NotImplementedError(
            f"bundle_builder: quantum_technology={tech!r} not yet supported. "
            f"Phase 3 ships only 'simulation'; Phase 4 will add "
            f"'superconducting' (Qiskit), 'trapped_ion' (Braket / IonQ), "
            f"'photonic' (Braket Borealis / Xanadu), 'neutral_atom' "
            f"(QuEra / Pasqal). Submitted selection: provider_id="
            f"{selection.provider_id!r}, backend_name="
            f"{selection.backend_name!r}."
        )

    return ProviderSubmission(
        bundle_kind=bundle_kind,
        payload=payload,
        shots=shots,
        bundle_hash=_bundle_hash(payload),
        metadata={
            "request_id": request_id,
            "provider_id": selection.provider_id,
            "backend_name": selection.backend_name,
            "quantum_technology": tech,
        },
    )
