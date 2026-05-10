"""Phoenix state-backend package.

Phase 6b Step 4 exports the factory at the package level so call sites
read cleanly as ``from phoenix.state import get_state_backend``.
"""

from phoenix.state.factory import (
    InvalidBackendConfig,
    get_state_backend,
    reset_state_backend,
)

__all__ = [
    "InvalidBackendConfig",
    "get_state_backend",
    "reset_state_backend",
]
