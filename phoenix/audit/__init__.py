"""Phoenix structured audit log (Phase 7).

Per architecture v1 Section 1 Decision 16: every Trinity Core layer
transition, router decision, authentication check, drift alert, and
config change emits a structured audit event. Phoenix uses a native
event format internally (this package); the OpenTelemetry adapter
(Step 3) translates events to OTLP for external observability
backends.

Phase 7 Step 1 ships the event format, the JSONL sink (default
``~/.phoenix/runtime/audit/events-YYYY-MM-DD.jsonl``), and the
singleton emitter. Step 2 wires call sites across the safety + verification
gates and the REST/WS surface. Step 3 adds the OTel sink.
"""

from phoenix.audit.emitter import (
    AuditEmitter,
    AuditSink,
    get_emitter,
    reset_emitter,
)
from phoenix.audit.event_format import AuditEvent, event_from_dict
from phoenix.audit.jsonl_writer import JSONLWriter

__all__ = [
    "AuditEmitter",
    "AuditEvent",
    "AuditSink",
    "JSONLWriter",
    "event_from_dict",
    "get_emitter",
    "reset_emitter",
]
