"""Phase 7 Step 3 -- OpenTelemetry export adapter tests.

The adapter (:class:`phoenix.audit.otel_adapter.OTelExporter`) translates
:class:`AuditEvent` instances to OpenTelemetry log records and exports
via OTLP HTTP/protobuf. These tests verify:

- :func:`OTelExporter.from_env` returns ``None`` when the activation
  flag is unset and a wired exporter when it is set.
- :class:`AuditEvent` → OTel log-record conversion preserves
  ``event_type`` (body), ``layer``, ``actor_id``, ``request_id``, and
  the ``parameters`` dict (flattened with dotted keys; non-scalar
  values JSON-encoded).
- Severity mapping: ``denied.*`` → WARN, ``error`` → ERROR, others →
  INFO.
- The adapter implements the
  :class:`~phoenix.audit.emitter.AuditSink` Protocol cleanly:
  :meth:`emit` swallows exceptions; :meth:`close` is idempotent.
- Lifespan wiring: when ``$PHOENIX_OTEL_ENABLED=1`` is set BEFORE the
  daemon starts, the sink lands on the singleton emitter; when unset,
  no opentelemetry import is attempted.

Tests use a :class:`SimpleLogRecordProcessor` + in-memory recording
exporter so no real OTLP collector is required. The recording exporter
is the canonical pattern from the OpenTelemetry test suite.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

opentelemetry = pytest.importorskip(
    "opentelemetry.sdk._logs",
    reason="OTel adapter tests require the [otel] extra installed.",
)

from opentelemetry.sdk._logs import LoggerProvider, LogRecordProcessor  # noqa: E402
from opentelemetry.sdk._logs.export import (  # noqa: E402
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)

import phoenix  # noqa: F401, E402  -- triggers sys.path injection
from phoenix.audit import AuditEvent, get_emitter, reset_emitter  # noqa: E402
from phoenix.audit.otel_adapter import (  # noqa: E402
    OTLP_ENDPOINT_ENV,
    PHOENIX_OTEL_ENABLED_ENV,
    OTelExporter,
    _flatten_attributes,
    _severity_for,
)


@pytest.fixture
def memory_exporter_and_provider() -> Iterator[
    tuple[InMemoryLogRecordExporter, LoggerProvider, LogRecordProcessor]
]:
    """Build a real OTel LoggerProvider with an in-memory recording exporter.

    Tests inject this provider into the OTelExporter constructor via
    the ``_logger_provider`` test-seam parameter so they can verify the
    log records the adapter produces without spinning up a real OTLP
    collector. SimpleLogRecordProcessor exports synchronously so the
    test sees the record immediately after emit.
    """
    exporter = InMemoryLogRecordExporter()
    processor = SimpleLogRecordProcessor(exporter)
    provider = LoggerProvider()
    provider.add_log_record_processor(processor)
    yield exporter, provider, processor
    provider.shutdown()


# ---------------------------------------------------------------------------
# Severity mapping


class TestSeverityMapping:
    def test_denied_event_maps_to_warn(self) -> None:
        from opentelemetry._logs import SeverityNumber

        sev_num, sev_text = _severity_for("safety.gate.denied.auth")
        assert sev_text == "WARN"
        # WARN per OTel logs spec.
        assert sev_num == SeverityNumber.WARN
        assert sev_num.value == 13

    def test_error_event_maps_to_error(self) -> None:
        _, sev_text = _severity_for("api.request.error")
        assert sev_text == "ERROR"

    def test_failed_event_maps_to_warn(self) -> None:
        _, sev_text = _severity_for("verification.gate.task.failed")
        assert sev_text == "WARN"

    def test_allowed_event_maps_to_info(self) -> None:
        from opentelemetry._logs import SeverityNumber

        sev_num, sev_text = _severity_for("safety.gate.allowed")
        assert sev_text == "INFO"
        assert sev_num == SeverityNumber.INFO
        assert sev_num.value == 9

    def test_lifecycle_event_maps_to_info(self) -> None:
        _, sev_text = _severity_for("verification.gate.completed")
        assert sev_text == "INFO"


# ---------------------------------------------------------------------------
# Attribute flattening


class TestAttributeFlattening:
    def test_scalar_parameters_pass_through(self) -> None:
        event = AuditEvent(
            timestamp_unix=1.0,
            actor_id="ash",
            layer="safety_gate",
            event_type="safety.gate.allowed",
            parameters={"cost": 5, "tier": "default", "is_admin": False},
            result_hash="",
            request_id="req_x",
        )
        attrs = _flatten_attributes(event)
        assert attrs["event_type"] == "safety.gate.allowed"
        assert attrs["layer"] == "safety_gate"
        assert attrs["actor_id"] == "ash"
        assert attrs["request_id"] == "req_x"
        assert attrs["parameters.cost"] == 5
        assert attrs["parameters.tier"] == "default"
        assert attrs["parameters.is_admin"] is False

    def test_non_scalar_parameters_json_encoded(self) -> None:
        event = AuditEvent(
            timestamp_unix=1.0,
            actor_id="ash",
            layer="t",
            event_type="t.x",
            parameters={"list_val": [1, 2, 3], "dict_val": {"a": 1}},
            result_hash="",
        )
        attrs = _flatten_attributes(event)
        # Lists + dicts JSON-encoded to keep OTel attribute types happy.
        assert attrs["parameters.list_val"] == "[1, 2, 3]"
        assert attrs["parameters.dict_val"] == '{"a": 1}'

    def test_none_parameter_becomes_empty_string(self) -> None:
        event = AuditEvent(
            timestamp_unix=1.0,
            actor_id="ash",
            layer="t",
            event_type="t.x",
            parameters={"maybe": None},
            result_hash="",
        )
        attrs = _flatten_attributes(event)
        assert attrs["parameters.maybe"] == ""

    def test_no_request_id_omits_attribute(self) -> None:
        event = AuditEvent(
            timestamp_unix=1.0,
            actor_id="ash",
            layer="t",
            event_type="t.x",
            parameters={},
            result_hash="",
            request_id=None,
        )
        attrs = _flatten_attributes(event)
        assert "request_id" not in attrs


# ---------------------------------------------------------------------------
# OTelExporter.from_env


class TestFromEnv:
    def test_returns_none_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PHOENIX_OTEL_ENABLED_ENV, raising=False)
        assert OTelExporter.from_env() is None

    def test_returns_none_when_flag_is_not_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PHOENIX_OTEL_ENABLED_ENV, "true")  # not "1"
        assert OTelExporter.from_env() is None

    def test_returns_exporter_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PHOENIX_OTEL_ENABLED_ENV, "1")
        # Use a localhost endpoint that the OTLP exporter will try to hit
        # on first export; with BatchLogRecordProcessor it queues until
        # flush, so construction succeeds without a real collector.
        monkeypatch.setenv(OTLP_ENDPOINT_ENV, "http://127.0.0.1:9999/v1/logs")
        exporter = OTelExporter.from_env()
        try:
            assert exporter is not None
            assert isinstance(exporter, OTelExporter)
        finally:
            if exporter is not None:
                exporter.close(drain_timeout_seconds=0.1)


# ---------------------------------------------------------------------------
# Exporter emit + close (via injected in-memory provider)


class TestEmitConversion:
    def test_emit_produces_log_record_with_correct_shape(
        self,
        memory_exporter_and_provider: tuple[
            InMemoryLogRecordExporter, LoggerProvider, LogRecordProcessor
        ],
    ) -> None:
        memory_exporter, provider, _processor = memory_exporter_and_provider
        sink = OTelExporter(_logger_provider=provider)
        event = AuditEvent(
            timestamp_unix=1717400000.5,
            actor_id="ash",
            layer="safety_gate",
            event_type="safety.gate.denied.auth",
            parameters={"reason": "bad_name", "cost_deducted": 0},
            result_hash="",
            request_id="req_otel_1",
        )
        sink.emit(event)
        records = memory_exporter.get_finished_logs()
        assert len(records) == 1
        rec = records[0].log_record
        # Body carries the dot-namespaced event_type for SIEM filtering.
        assert rec.body == "safety.gate.denied.auth"
        # Severity reflects the denied outcome.
        assert rec.severity_text == "WARN"
        # Timestamp converted to nanoseconds.
        assert rec.timestamp == 1717400000500000000
        # Attributes preserve correlation keys + flattened params.
        assert rec.attributes is not None
        assert rec.attributes["event_type"] == "safety.gate.denied.auth"
        assert rec.attributes["layer"] == "safety_gate"
        assert rec.attributes["actor_id"] == "ash"
        assert rec.attributes["request_id"] == "req_otel_1"
        assert rec.attributes["parameters.reason"] == "bad_name"
        assert rec.attributes["parameters.cost_deducted"] == 0

    def test_emit_after_close_is_noop(
        self,
        memory_exporter_and_provider: tuple[
            InMemoryLogRecordExporter, LoggerProvider, LogRecordProcessor
        ],
    ) -> None:
        memory_exporter, provider, _processor = memory_exporter_and_provider
        sink = OTelExporter(_logger_provider=provider)
        sink.close(drain_timeout_seconds=0.1)
        sink.emit(
            AuditEvent(
                timestamp_unix=1.0,
                actor_id="a",
                layer="t",
                event_type="t.x",
                parameters={},
                result_hash="",
            )
        )
        assert memory_exporter.get_finished_logs() == ()

    def test_close_is_idempotent(
        self,
        memory_exporter_and_provider: tuple[
            InMemoryLogRecordExporter, LoggerProvider, LogRecordProcessor
        ],
    ) -> None:
        _, provider, _processor = memory_exporter_and_provider
        sink = OTelExporter(_logger_provider=provider)
        sink.close()
        sink.close()  # must not raise

    def test_emit_exception_does_not_propagate(
        self,
        memory_exporter_and_provider: tuple[
            InMemoryLogRecordExporter, LoggerProvider, LogRecordProcessor
        ],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Synthetic failure in the OTel logger.emit must not propagate."""
        _, provider, _processor = memory_exporter_and_provider
        sink = OTelExporter(_logger_provider=provider)

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("synthetic OTel failure")

        monkeypatch.setattr(sink._logger, "emit", _boom)
        # Must not raise -- per fire-and-forget audit contract.
        sink.emit(
            AuditEvent(
                timestamp_unix=time.time(),
                actor_id="a",
                layer="t",
                event_type="t.x",
                parameters={},
                result_hash="",
            )
        )


# ---------------------------------------------------------------------------
# Integration with the singleton emitter (fan-out alongside JSONL)


class TestEmitterIntegration:
    def test_otel_sink_co_exists_with_jsonl_sink(
        self,
        memory_exporter_and_provider: tuple[
            InMemoryLogRecordExporter, LoggerProvider, LogRecordProcessor
        ],
        tmp_path: pytest.TempPathFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When both sinks are attached, one emit fans out to both."""
        monkeypatch.setenv("PHOENIX_AUDIT_DIR", str(tmp_path))  # type: ignore[arg-type]
        reset_emitter()
        memory_exporter, provider, _processor = memory_exporter_and_provider
        try:
            emitter = get_emitter()  # ships with JSONL sink by default
            otel_sink = OTelExporter(_logger_provider=provider)
            emitter.add_sink(otel_sink)

            event = AuditEvent(
                timestamp_unix=time.time(),
                actor_id="ash",
                layer="safety_gate",
                event_type="safety.gate.allowed",
                parameters={"cost": 1},
                result_hash="",
                request_id="req_both_sinks",
            )
            emitter.emit(event)

            # OTel sink received it.
            records = memory_exporter.get_finished_logs()
            assert len(records) == 1
            assert records[0].log_record.body == "safety.gate.allowed"
        finally:
            reset_emitter()


# ---------------------------------------------------------------------------
# Lifespan wiring (Phase 7 Step 3 -- registers OTel sink on daemon startup)


class TestLifespanWiring:
    def test_lifespan_skips_otel_sink_when_env_disabled(
        self,
        tmp_path: pytest.TempPathFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With $PHOENIX_OTEL_ENABLED unset, daemon startup must NOT add an
        OTelExporter sink -- the OTel SDK is not even imported on installs
        without the [otel] extra. We verify by counting sinks after lifespan."""
        from fastapi.testclient import TestClient

        from phoenix.api.routes import app

        monkeypatch.delenv(PHOENIX_OTEL_ENABLED_ENV, raising=False)
        monkeypatch.setenv("PHOENIX_AUDIT_DIR", str(tmp_path))  # type: ignore[arg-type]
        reset_emitter()
        try:
            with TestClient(app) as client:
                assert client.get("/v1/health").status_code == 200
                # After startup, the emitter has its default JSONL sink only.
                sinks = list(get_emitter()._sinks)  # type: ignore[attr-defined]
                otel_sinks = [s for s in sinks if isinstance(s, OTelExporter)]
                assert otel_sinks == []
        finally:
            reset_emitter()

    def test_lifespan_attaches_otel_sink_when_env_enabled(
        self,
        memory_exporter_and_provider: tuple[
            InMemoryLogRecordExporter, LoggerProvider, LogRecordProcessor
        ],
        tmp_path: pytest.TempPathFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With $PHOENIX_OTEL_ENABLED=1, daemon startup adds the OTel sink
        and audit events fan out to it. We monkey-patch from_env to inject
        a test provider so we don't make a real OTLP TCP connection."""
        from fastapi.testclient import TestClient

        from phoenix.api.routes import app
        from phoenix.audit import otel_adapter

        memory_exporter, provider, _processor = memory_exporter_and_provider
        monkeypatch.setenv(PHOENIX_OTEL_ENABLED_ENV, "1")
        monkeypatch.setenv("PHOENIX_AUDIT_DIR", str(tmp_path))  # type: ignore[arg-type]

        def _from_env_test() -> OTelExporter:
            return OTelExporter(_logger_provider=provider)

        monkeypatch.setattr(otel_adapter.OTelExporter, "from_env", _from_env_test)
        reset_emitter()
        try:
            with TestClient(app) as client:
                # Hit /v1/health -- HTTP middleware emits api.request.* events.
                assert client.get("/v1/health").status_code == 200
                # OTel exporter should have received the api.request emits.
                records = memory_exporter.get_finished_logs()
                event_types = {r.log_record.body for r in records}
                assert "api.request.start" in event_types
                assert "api.request.complete" in event_types
        finally:
            reset_emitter()
