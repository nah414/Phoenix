"""OpenTelemetry export adapter for the audit emitter (Phase 7 Step 3).

Per architecture v1 Section 1 Decision 22: Phoenix's native audit format
is :class:`AuditEvent` (a typed dataclass written to JSONL by default);
the OTel adapter is an **opt-in** second sink that translates each event
to an OpenTelemetry log record and exports via OTLP HTTP/protobuf. This
lets regulated users plug Phoenix into their existing SIEM/observability
stack (Datadog, Splunk, Honeycomb, Grafana Tempo, ...) without making
OTel a hard runtime dependency.

**Activation:** the adapter is registered as a sink on the singleton
:class:`~phoenix.audit.emitter.AuditEmitter` at FastAPI lifespan startup
when ``$PHOENIX_OTEL_ENABLED=1`` is set. The endpoint follows the
standard OTel env var ``$OTEL_EXPORTER_OTLP_ENDPOINT`` (no Phoenix-
specific override). When the flag is unset, :func:`from_env` returns
``None`` and the adapter is never imported -- import-safe even when the
``otel`` extra is not installed.

**Install:** ``pip install phoenix-middleware[otel]`` pulls in
``opentelemetry-sdk>=1.20,<2.0`` and
``opentelemetry-exporter-otlp-proto-http>=1.20,<2.0`` per the
2026-05-11-locked open item 2 (HTTP/protobuf default; gRPC available
in 1.x).

**Severity mapping:** :class:`AuditEvent` event types follow the
``layer.subsystem.outcome`` convention (e.g. ``safety.gate.denied.auth``).
This module maps ``denied.*`` / ``error`` / ``failed`` outcomes to OTel
``WARN`` or ``ERROR``; everything else is ``INFO``. The mapping is
deliberately simple -- downstream SIEM rules can be tuned on the
``event_type`` attribute itself, which always carries the full
dot-namespaced string.

**Fire-and-forget contract:** identical to the JSONL writer. The
:class:`OTelExporter` swallows all exceptions during ``emit`` so a
broken OTLP endpoint cannot wedge the solve hot path. The
:class:`~phoenix.audit.emitter.AuditEmitter` already isolates sink
exceptions, but the adapter belt-and-suspenders to be safe.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opentelemetry._logs import SeverityNumber
    from opentelemetry.sdk._logs import LoggerProvider, LogRecordProcessor

from phoenix.audit.event_format import AuditEvent

logger = logging.getLogger(__name__)


# Default OTLP endpoint per OTel spec when no env var is set; the standard
# collector listens on 4318/v1/logs for HTTP/protobuf. We don't hard-fall-
# back to this -- if PHOENIX_OTEL_ENABLED=1 but no endpoint is configured,
# the OTLPLogExporter applies its own default (the OTel SDK looks up
# OTEL_EXPORTER_OTLP_LOGS_ENDPOINT, then OTEL_EXPORTER_OTLP_ENDPOINT, then
# falls back to http://localhost:4318/v1/logs). We just forward.
PHOENIX_OTEL_ENABLED_ENV = "PHOENIX_OTEL_ENABLED"
OTLP_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"


def _severity_for(event_type: str) -> tuple[SeverityNumber, str]:
    """Map an :class:`AuditEvent` event_type string to an OTel severity.

    Returns ``(severity_number, severity_text)``. The severity number is
    the :class:`SeverityNumber` enum per the OTel logs spec; the text is
    the canonical upper-case label.

    Heuristic (lazy import of :class:`SeverityNumber` to keep the module
    import-safe without the otel extra):

    - ``error`` / ``exception`` in the type → ERROR (17)
    - ``denied`` / ``rejected`` / ``failed`` in the type → WARN (13)
    - everything else → INFO (9)

    Downstream SIEM rules should key off the ``event_type`` attribute
    directly rather than relying on this mapping; the mapping exists so
    that out-of-the-box OTel pipelines (which often filter on severity)
    don't drop legitimate denial events as INFO-level noise.
    """
    from opentelemetry._logs import SeverityNumber

    lowered = event_type.lower()
    if "error" in lowered or "exception" in lowered:
        return SeverityNumber.ERROR, "ERROR"
    if "denied" in lowered or "rejected" in lowered or "failed" in lowered:
        return SeverityNumber.WARN, "WARN"
    return SeverityNumber.INFO, "INFO"


def _flatten_attributes(event: AuditEvent) -> dict[str, Any]:
    """Convert an :class:`AuditEvent` into the flat attribute dict that
    OTel logs expect.

    OTel log records accept ``attributes: dict[str, AttributeValue]`` where
    ``AttributeValue`` is ``str | int | float | bool | Sequence[...]``.
    Phoenix audit event parameters can be arbitrary JSON-serializable
    dicts; we flatten them with dotted keys and coerce non-scalar values
    to JSON strings so OTel never drops them.

    The full ``event_type``, ``layer``, ``actor_id``, ``request_id``, and
    ``result_hash`` are always preserved at the top level for SIEM
    filtering. The ``parameters`` sub-dict is exposed under the
    ``parameters.<key>`` namespace so backends with limited attribute
    counts can still find what they need at the top level.
    """
    import json

    attrs: dict[str, Any] = {
        "event_type": event.event_type,
        "layer": event.layer,
        "actor_id": event.actor_id,
        "result_hash": event.result_hash,
    }
    if event.request_id is not None:
        attrs["request_id"] = event.request_id
    for key, value in event.parameters.items():
        attr_key = f"parameters.{key}"
        if value is None:
            attrs[attr_key] = ""
        elif isinstance(value, str | int | float | bool):
            attrs[attr_key] = value
        else:
            # Lists, dicts, etc. -- JSON-encode to keep the attribute set
            # OTel-compatible. SIEM filters can JSON-parse the string.
            attrs[attr_key] = json.dumps(value, default=str, ensure_ascii=False)
    return attrs


class OTelExporter:
    """Audit sink that exports events to an OTel-compatible collector.

    Constructed eagerly (the OTel SDK + OTLP HTTP exporter are imported
    on instantiation, not at module load) so that a Phoenix install
    without the ``otel`` extra never tries to ``import opentelemetry``.

    Implements the :class:`~phoenix.audit.emitter.AuditSink` Protocol:
    ``emit(event)`` + ``close(drain_timeout_seconds)``.

    The constructor accepts an optional ``_logger_provider`` for test
    injection -- tests pass a :class:`LoggerProvider` pre-wired with a
    :class:`SimpleLogRecordProcessor` + in-memory recording exporter so
    they can verify the conversion without spinning up a real collector.
    Production callers use :meth:`from_env` which builds the standard
    ``BatchLogRecordProcessor`` + ``OTLPLogExporter`` chain.
    """

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        logger_name: str = "phoenix.audit",
        _logger_provider: LoggerProvider | None = None,
    ) -> None:
        """Construct the exporter.

        ``endpoint`` is the OTLP HTTP/protobuf URL (e.g.
        ``http://collector:4318/v1/logs``). When ``None``, the OTel SDK
        resolves it from ``$OTEL_EXPORTER_OTLP_LOGS_ENDPOINT`` →
        ``$OTEL_EXPORTER_OTLP_ENDPOINT`` → its internal default.

        ``_logger_provider`` is the test-injection seam. When provided,
        the exporter skips the default OTLP wiring and uses the caller's
        provider verbatim.
        """
        # Lazy imports: the otel extra may not be installed at all on
        # SQLite-only installs that don't opt in to OTel. Phoenix code
        # paths that don't touch this class never trip the imports.
        self._processor: LogRecordProcessor | None
        if _logger_provider is None:
            from opentelemetry.exporter.otlp.proto.http._log_exporter import (
                OTLPLogExporter,
            )
            from opentelemetry.sdk._logs import LoggerProvider
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

            self._owns_provider = True
            self._logger_provider = LoggerProvider()
            exporter = OTLPLogExporter(endpoint=endpoint) if endpoint else OTLPLogExporter()
            self._processor = BatchLogRecordProcessor(exporter)
            self._logger_provider.add_log_record_processor(self._processor)
        else:
            # Caller (typically tests) owns the provider lifecycle.
            self._owns_provider = False
            self._logger_provider = _logger_provider
            self._processor = None

        self._logger = self._logger_provider.get_logger(logger_name)
        self._closed = False

    @classmethod
    def from_env(cls) -> OTelExporter | None:
        """Build an exporter from environment variables, or return ``None``.

        Returns the constructed exporter when
        ``$PHOENIX_OTEL_ENABLED == "1"``; otherwise returns ``None`` so
        the caller (typically the FastAPI lifespan startup) can skip
        registration without an exception. The OTLP endpoint follows
        the standard OTel env var ``$OTEL_EXPORTER_OTLP_ENDPOINT``.

        Lazy import-aware: this method does NOT import ``opentelemetry``
        when the env flag is disabled. Construction (and therefore the
        OTel SDK import) only happens when the flag is set.
        """
        if os.environ.get(PHOENIX_OTEL_ENABLED_ENV) != "1":
            return None
        endpoint = os.environ.get(OTLP_ENDPOINT_ENV)
        return cls(endpoint=endpoint)

    def emit(self, event: AuditEvent) -> None:
        """Translate an :class:`AuditEvent` to an OTel log record and dispatch.

        Wrapped in try/except: a misbehaving OTLP endpoint (DNS failure,
        413 payload too large, TLS handshake error) must NOT propagate
        into the audit emit hot path. The :class:`AuditEmitter` itself
        also isolates sink exceptions, but we belt-and-suspenders here
        so the OTel adapter is independently resilient.
        """
        if self._closed:
            return
        try:
            severity_number, severity_text = _severity_for(event.event_type)
            timestamp_ns = int(event.timestamp_unix * 1_000_000_000)
            self._logger.emit(
                timestamp=timestamp_ns,
                observed_timestamp=timestamp_ns,
                severity_number=severity_number,
                severity_text=severity_text,
                body=event.event_type,
                attributes=_flatten_attributes(event),
            )
        except Exception:
            logger.exception("OTel sink emit failed for event_type=%r", event.event_type)

    def close(self, drain_timeout_seconds: float = 5.0) -> None:
        """Flush pending records and shut down the OTel pipeline.

        Idempotent: calling close twice is a no-op (matches the
        :class:`~phoenix.audit.jsonl_writer.JSONLWriter` contract).

        Only the components this exporter constructed are torn down --
        tests that injected their own LoggerProvider keep ownership of
        its shutdown.
        """
        if self._closed:
            return
        self._closed = True
        timeout_ms = int(drain_timeout_seconds * 1000)
        try:
            if self._processor is not None:
                self._processor.force_flush(timeout_ms)
        except Exception:
            logger.exception("OTel processor force_flush failed during close")
        try:
            if self._owns_provider:
                self._logger_provider.shutdown()
        except Exception:
            logger.exception("OTel logger provider shutdown failed")


__all__ = [
    "OTLP_ENDPOINT_ENV",
    "OTelExporter",
    "PHOENIX_OTEL_ENABLED_ENV",
]
