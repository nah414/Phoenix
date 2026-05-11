"""Typed ledger-entry shapes (Phase 7 Step 4).

Per architecture v1 line 2243, ``entry_types.py`` holds the typed
dataclasses for each kind of ledger entry Phoenix can append:

- :class:`SolveEntry` -- a completed solve. Carries the full
  :class:`~phoenix.trinity.data_model.ProvenanceTrace` plus the
  agreement classification + KPI bundle hash so the replay engine
  (Phase 7 Step 7) has everything it needs to reconstruct the run.
- :class:`OverrideByOperatorEntry` -- Section 7.6's operator override
  for ``HUMAN_REVIEW`` results from the verification gate.
- :class:`KillSwitchEntry` -- Section 7.5's kill-switch engage/release
  events. Each transition is its own ledger entry.
- :class:`EnrollmentEntry` -- Section 7.3's new-actor enrollment.

The four typed dataclasses are deliberately payload-focused (not
subclasses of :class:`LedgerEntry`). The on-chain record is always
the common :class:`LedgerEntry` shape -- a typed payload dataclass
is converted to a generic dict via :func:`to_ledger_entry` before
being passed to :class:`~phoenix.ledger.omega_ledger.OmegaLedger`.

This split keeps three properties:

1. The vendored Omega Ledger primitive (:class:`vendor.omega.ledger`)
   sees a single uniform shape.
2. Typed-dataclass callers get Python-side type checking on the
   fields they care about.
3. Phase 7 Step 6 (ledger composition) can extend the payload
   schema additively without breaking the on-chain layout.

Per the locked Phase 7 scope (2026-05-11), only these four entry
kinds ship in v1. The architecture spec lists more (
``KILL_SWITCH_RELEASED``, ``REVOCATION``, ``PROPOSED_BY_AGENT``)
which land in v1.x as needed.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# On-chain record shape.


@dataclass(frozen=True)
class LedgerEntry:
    """The on-chain record stored in the hashchain.

    Fields:

    - ``entry_id`` -- UUID4 string; primary key.
    - ``entry_kind`` -- discriminator (``"solve"``,
      ``"override_by_operator"``, ``"kill_switch"``, ``"enrollment"``).
      Routes the payload back to the typed dataclass on read.
    - ``timestamp_unix`` -- when the entry was created.
    - ``actor_id`` -- lowercase Actor name responsible for the event.
    - ``parent_hash`` -- previous entry's ``entry_hash``, or
      :data:`vendor.omega.ledger.GENESIS_HASH` for the first entry.
      **Set by** :meth:`OmegaLedger.append_entry`; callers leave it as
      the empty string.
    - ``entry_hash`` -- ``SHA-256(parent_hash | entry_kind | payload_json)``.
      Also set by :meth:`OmegaLedger.append_entry`.
    - ``payload`` -- the typed-dataclass-converted dict. Schema depends
      on ``entry_kind``; the typed dataclasses below document each
      kind's keys.
    """

    entry_id: str
    entry_kind: str
    timestamp_unix: float
    actor_id: str
    parent_hash: str
    entry_hash: str
    payload: dict[str, Any]

    def to_canonical_payload_json(self) -> str:
        """Canonical JSON encoding of ``payload`` used by the hashchain.

        Stable across processes + Python versions: ``sort_keys=True``,
        ``ensure_ascii=True``, ``allow_nan=False``, no whitespace.
        This is the exact string the vendored
        :func:`vendor.omega.ledger._compute_entry_hash` sees as the
        ``contract_json`` argument, so deterministic JSON encoding is
        load-bearing for replay verification.
        """
        return json.dumps(
            self.payload,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )


# ---------------------------------------------------------------------------
# Typed payload dataclasses -- one per entry_kind.


@dataclass(frozen=True)
class SolveEntry:
    """Payload schema for a completed ``POST /v1/tasks`` request.

    Phase 7 Step 4 ships the **shape** (typed payload + helpers).
    Phase 7 Step 6 wires the **content** -- the verification gate
    will populate these fields from the live VerificationProvenance,
    RoutingProvenance, and TrinityCoreTrace per architecture §6.7.

    Field semantics:

    - ``task_id`` / ``request_id`` -- typically equal; the front-door
      UUID. ``task_id`` is the API-surface name, ``request_id`` is the
      lower-layer correlation key. Kept distinct so future routes
      with different keying don't have to change the schema.
    - ``rung_used`` -- final verification rung (R1..R5).
    - ``agreement_type`` -- vendored DisagreementType enum value.
    - ``result_value`` / ``error_bar`` / ``sigma`` -- final Result envelope.
    - ``result_hash`` -- canonical SHA-256 of the Result envelope; the
      replay engine recomputes this and refuses to ship if it diverges.
    - ``verification_provenance`` / ``routing_provenance`` /
      ``trinity_core_trace`` -- nested dicts. Phase 7 Step 6 populates
      from live provenance objects via their existing ``_to_dict``
      helpers; Step 4 leaves them as ``{}`` placeholders.
    - ``calibration_profile_hash`` -- pinned hash of the vendor
      ``calibration_profile.json`` at solve time. Drives replay
      version verification per architecture §10.4.
    - ``vendor_manifest`` -- snapshot of ``vendor/VENDOR_VERSION.txt``
      contents (the dr_frank_and_eddy_commit + phoenix_release pair).
    """

    task_id: str
    request_id: str
    rung_used: str
    agreement_type: str
    result_value: float
    error_bar: float
    sigma: float
    result_hash: str
    verification_provenance: dict[str, Any] = field(default_factory=dict)
    routing_provenance: dict[str, Any] = field(default_factory=dict)
    trinity_core_trace: dict[str, Any] = field(default_factory=dict)
    calibration_profile_hash: str = ""
    vendor_manifest: dict[str, Any] = field(default_factory=dict)
    # Phase 7 Step 7 -- reproducibility-mode environment snapshot.
    # Empty dict for ``default`` mode; populated by
    # :func:`phoenix._internal.reproducibility.capture_environment` for
    # ``strict`` and ``replay`` modes. Replay reads this back and calls
    # :func:`restore_environment` before re-running the deterministic
    # portion of the pipeline.
    reproducibility_mode: str = "default"
    environment_snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OverrideByOperatorEntry:
    """Payload schema for Section 7.6's operator override events.

    Fields:

    - ``operator_id`` -- the admin actor that issued the override.
    - ``affected_task_id`` -- which task's HUMAN_REVIEW result was
      overridden.
    - ``override_disposition`` -- the operator's chosen disposition.
      Per architecture §7.6 the valid values are ``"ship-as-degraded"``,
      ``"reject"``, ``"re-run-with-tighter-bounds"``. Free-form here so
      v1.x can add more without a schema break.
    - ``reason`` -- operator's plain-text justification (audit-only).
    """

    operator_id: str
    affected_task_id: str
    override_disposition: str
    reason: str


@dataclass(frozen=True)
class KillSwitchEntry:
    """Payload schema for Section 7.5's kill-switch transitions.

    One entry per state transition. ``transition`` is the discriminator
    between engage and release, kept inline so a single ``entry_kind``
    ("kill_switch") covers both rather than splitting into two kinds.

    Fields:

    - ``transition`` -- ``"engaged"`` or ``"released"``.
    - ``by`` -- actor name that triggered the transition.
    - ``reason`` -- free-form rationale (audit-only).
    - ``engaged_at_unix`` -- only set on ``release`` events; allows
      release-side ledger entries to point back at the engagement.
    """

    transition: str
    by: str
    reason: str
    engaged_at_unix: float | None = None


@dataclass(frozen=True)
class EnrollmentEntry:
    """Payload schema for Section 7.3's new-actor enrollment events.

    Fields:

    - ``enrolled_actor_name`` -- the new actor's lowercase ASCII name.
    - ``enrolled_by`` -- admin actor that performed the enrollment.
    - ``permissions`` -- snapshot of the
      :class:`~phoenix.safety.permissions.ActorPermissions` granted
      at enrollment time (as a dict; the actor's permissions can
      later change via separate registry edits, but the enrollment
      snapshot is immutable on the chain).
    - ``identity_fingerprint`` -- the new actor's identity fingerprint
      (per the vendored Actor pattern, Section 7.2).
    """

    enrolled_actor_name: str
    enrolled_by: str
    permissions: dict[str, Any]
    identity_fingerprint: str = ""


# ---------------------------------------------------------------------------
# Typed-payload -> LedgerEntry converters.


def _new_ledger_entry(
    *,
    entry_kind: str,
    actor_id: str,
    payload_dict: dict[str, Any],
    timestamp_unix: float | None = None,
) -> LedgerEntry:
    """Build a fresh :class:`LedgerEntry` with parent_hash/entry_hash
    left blank for :meth:`OmegaLedger.append_entry` to fill in."""
    return LedgerEntry(
        entry_id=str(uuid.uuid4()),
        entry_kind=entry_kind,
        timestamp_unix=timestamp_unix if timestamp_unix is not None else time.time(),
        actor_id=actor_id,
        parent_hash="",
        entry_hash="",
        payload=payload_dict,
    )


def solve_to_ledger_entry(
    solve: SolveEntry,
    *,
    actor_id: str,
    timestamp_unix: float | None = None,
) -> LedgerEntry:
    """Convert a :class:`SolveEntry` payload to a :class:`LedgerEntry`."""
    return _new_ledger_entry(
        entry_kind="solve",
        actor_id=actor_id,
        payload_dict=asdict(solve),
        timestamp_unix=timestamp_unix,
    )


def override_to_ledger_entry(
    override: OverrideByOperatorEntry,
    *,
    timestamp_unix: float | None = None,
) -> LedgerEntry:
    """Convert an :class:`OverrideByOperatorEntry` to a :class:`LedgerEntry`.

    The operator's actor name is also the ``actor_id`` of the wrapping
    ledger entry, since they're responsible for the override.
    """
    return _new_ledger_entry(
        entry_kind="override_by_operator",
        actor_id=override.operator_id,
        payload_dict=asdict(override),
        timestamp_unix=timestamp_unix,
    )


def kill_switch_to_ledger_entry(
    event: KillSwitchEntry,
    *,
    timestamp_unix: float | None = None,
) -> LedgerEntry:
    """Convert a :class:`KillSwitchEntry` to a :class:`LedgerEntry`.

    The triggering actor (``event.by``) is the ledger entry's actor_id.
    """
    return _new_ledger_entry(
        entry_kind="kill_switch",
        actor_id=event.by,
        payload_dict=asdict(event),
        timestamp_unix=timestamp_unix,
    )


def enrollment_to_ledger_entry(
    enrollment: EnrollmentEntry,
    *,
    timestamp_unix: float | None = None,
) -> LedgerEntry:
    """Convert an :class:`EnrollmentEntry` to a :class:`LedgerEntry`.

    The enrolling admin actor is the ledger entry's actor_id (not the
    new actor being enrolled -- audit responsibility tracks the admin).
    """
    return _new_ledger_entry(
        entry_kind="enrollment",
        actor_id=enrollment.enrolled_by,
        payload_dict=asdict(enrollment),
        timestamp_unix=timestamp_unix,
    )


__all__ = [
    "EnrollmentEntry",
    "KillSwitchEntry",
    "LedgerEntry",
    "OverrideByOperatorEntry",
    "SolveEntry",
    "enrollment_to_ledger_entry",
    "kill_switch_to_ledger_entry",
    "override_to_ledger_entry",
    "solve_to_ledger_entry",
]
