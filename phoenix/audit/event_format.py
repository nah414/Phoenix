"""Native Phoenix audit-event format (Phase 7 Step 1).

Per architecture v1 Section 1 Decision 16: Phoenix's audit log uses a
native typed format internally; the OpenTelemetry adapter (Step 3,
per Decision 22) translates events to OTLP. This module defines the
:class:`AuditEvent` dataclass and its canonical JSON serialization.

**Why a native format, not OTel directly:**

- Phoenix can evolve the audit schema without breaking on OTel spec
  drift (Decision 22 explicitly: "OTel as the audit-log *export*
  standard, not the internal format").
- Standards-compliance for users who want OTLP is one adapter away;
  zero-dep ops monitors can read the JSONL directly.

**Required fields per Decision 16** (verbatim):

> Every Trinity Core layer transition (Solver-to-Control,
> Control-to-Orchestrate), every router decision, every authentication
> check, every drift alert, every config change emits a structured
> event with timestamp, actor identity, layer, parameters, and result
> hash.

This module's :class:`AuditEvent` captures: ``timestamp_unix``,
``actor_id``, ``layer``, ``event_type``, ``parameters`` (any JSON-able
dict), ``result_hash`` (sha256-prefixed string, or empty when the
event has no hashable result like a pure decision), ``request_id``
(optional — the front-door per-request UUID lets joins between audit
events and ledger entries work cleanly).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class AuditEvent:
    """One structured audit event.

    Stable shape across releases. The ``event_type`` discriminator is
    dot-namespaced by emitting layer:

    - ``safety.stage<N>.<outcome>`` (Phase 6a safety gate; Phase 7 Step 2
      wires emits at every stage)
    - ``verification.gate.<phase>`` (Phase 5 verification gate; Step 2)
    - ``api.request.<method>.<endpoint>`` (Phase 0+ REST/WS; Step 2)
    - ``drift.cycle.<state>`` (Phase 6b drift detector cycles)
    - ``ledger.entry.<kind>`` (Phase 7 Step 6 ledger appends)
    - ``admin.<action>`` (Phase 8 admin endpoints)

    The hierarchy is not enforced by code; it's a convention so audit
    consumers can filter by prefix.
    """

    timestamp_unix: float
    actor_id: str
    layer: str
    event_type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    result_hash: str = ""
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict.

        Stable field order (timestamp first, then identity, then event
        meta) so JSONL files are diff-readable in editors that don't
        pretty-print JSON.
        """
        return {
            "timestamp_unix": self.timestamp_unix,
            "actor_id": self.actor_id,
            "layer": self.layer,
            "event_type": self.event_type,
            "parameters": dict(self.parameters),
            "result_hash": self.result_hash,
            "request_id": self.request_id,
        }

    def to_json_line(self) -> str:
        """Serialize to one canonical JSON line + trailing newline.

        Canonical ordering: keys in the :meth:`to_dict` order (timestamp
        first) so a ``diff`` over the JSONL is informative. ``ensure_ascii``
        is True so the file is valid 7-bit ASCII regardless of payload
        contents (a Unicode reason string gets escaped, not raw-byte
        smuggled).
        """
        # asdict would order alphabetically; we want insertion order.
        record = self.to_dict()
        return json.dumps(record, ensure_ascii=True, sort_keys=False) + "\n"


def event_from_dict(record: dict[str, Any]) -> AuditEvent:
    """Inverse of :meth:`AuditEvent.to_dict`. Useful for replay tests
    and audit-log readers."""
    return AuditEvent(
        timestamp_unix=float(record["timestamp_unix"]),
        actor_id=str(record["actor_id"]),
        layer=str(record["layer"]),
        event_type=str(record["event_type"]),
        parameters=dict(record.get("parameters") or {}),
        result_hash=str(record.get("result_hash") or ""),
        request_id=record.get("request_id"),
    )


__all__ = ["AuditEvent", "event_from_dict"]


# ``asdict`` is imported above as a convenience for callers that want the
# raw dataclass dict; not used inside this module.
_unused = asdict
