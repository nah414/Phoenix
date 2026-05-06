"""Phase 0 integration test -- assert /v1/health responds with the expected contract.

This is the first endpoint test. It boots the FastAPI app via TestClient (no actual
network I/O) and verifies the response shape. Architecture v1 Section 5.2 specifies
the /v1/health contract; this test is the contract's executable witness.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from phoenix.api.routes import app


def test_health_returns_200_and_expected_shape() -> None:
    client = TestClient(app)
    response = client.get("/v1/health")
    assert response.status_code == 200
    body = response.json()

    # Required fields per Phase 0 contract.
    assert body["status"] == "ok"
    assert body["phoenix_version"] == "1.0.0.dev0"
    assert body["calibration_status"] == "not_loaded"  # Phase 0 placeholder
    assert "checked_at_utc" in body
    assert "vendor_manifest" in body

    # Vendor manifest is the Phase 0 placeholder (file exists with empty hashes).
    vendor = body["vendor_manifest"]
    assert vendor is not None
    assert vendor["phoenix_release"] == "1.0.0.dev0"
    assert vendor["vendor_synced_at"] == ""  # Phase 1 populates this
    assert vendor["dr_frank_and_eddy_commit"] == ""  # Phase 1 populates this


def test_openapi_schema_served() -> None:
    """Architecture v1 Section 5.2 says OpenAPI 3.1 ships at /v1/openapi.json."""
    client = TestClient(app)
    response = client.get("/v1/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["openapi"].startswith("3.")
    assert schema["info"]["version"] == "1.0.0.dev0"
    # /v1/health is registered.
    assert "/v1/health" in schema["paths"]
