"""Phoenix v1 -- front-door REST surface. Phase 0 ships /v1/health only.

The full surface specified in PHOENIX_ARCHITECTURE_v1.md Section 5.2 lands across
later phases:
- Tasks endpoints -- Phase 5+ (verification gate is the gating dependency).
- Audit/ledger -- Phase 7+.
- Admin -- Phase 8.
- Adapters -- Phase 9.
- Identity -- Phase 6 (state backend dependency).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI

from phoenix._internal.version import __version__, read_vendor_version

app = FastAPI(
    title="Phoenix",
    description="Production-grade quantum-accuracy middleware (Phase 0 skeleton)",
    version=__version__,
    openapi_url="/v1/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.get("/v1/health")
def health() -> dict[str, Any]:
    """Liveness/readiness probe per architecture v1 Section 5.2.

    Phase 0 returns: phoenix version, vendor-manifest read result, a static
    "calibration_status" of 'not_loaded' (drift monitoring lands in Phase 7),
    and the current UTC timestamp.
    """
    vendor = read_vendor_version()
    return {
        "status": "ok",
        "phoenix_version": __version__,
        "vendor_manifest": vendor,
        # Phase 0 placeholder; Phase 7 wires in drift monitoring.
        "calibration_status": "not_loaded",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    }
