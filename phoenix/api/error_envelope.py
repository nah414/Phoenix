"""Standard error envelope per architecture v1 Section 5.2.

Phase 0 ships the dataclass. Phase 5+ wires it into real handlers via
FastAPI's exception handlers; the envelope shape is the stable contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ErrorEnvelope:
    """Stable error envelope. Code matches typed exception names from architecture Section 3.7."""

    code: str
    message: str
    path: str
    request_id: str
    documentation_url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "path": self.path,
                "request_id": self.request_id,
                "documentation_url": self.documentation_url,
            }
        }
