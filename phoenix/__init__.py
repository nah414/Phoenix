"""Phoenix — production-grade quantum-accuracy middleware.

See PHOENIX_ARCHITECTURE_v1.md for the architecture spec.

Per architecture v1 Section 11.7.1 (resolved verbatim through v1), this
module injects ``vendor/`` into ``sys.path`` so vendored modules retain their
upstream import paths (``from synthesis.equations.base import EquationSolver``
rather than ``from phoenix.vendor.synthesis.equations.base ...``). The path
is appended (not prepended) so stdlib and installed packages always win in
name resolution; only Phoenix-specific module names (``synthesis``, ``wobble``,
``actor``, ``grammar``) fall through to ``vendor/``.
"""

import sys as _sys
from pathlib import Path as _Path

from phoenix._internal.version import __version__


def _inject_vendor_path() -> None:
    """Append ``C:/Phoenix/vendor`` to ``sys.path`` for vendored-module resolution.

    Append (not insert at 0) so stdlib + installed packages always win in name
    resolution; only Phoenix-specific module names (``synthesis``, ``wobble``,
    ``actor``, ``grammar``) fall through to vendor/. No-op if the directory
    does not exist (e.g. fresh Phoenix clone with vendor not yet synced).
    """
    vendor = _Path(__file__).resolve().parent.parent / "vendor"
    if vendor.exists() and str(vendor) not in _sys.path:
        _sys.path.append(str(vendor))


_inject_vendor_path()

__all__ = ["__version__"]
