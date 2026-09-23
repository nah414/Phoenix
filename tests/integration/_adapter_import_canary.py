"""Import canary for ``test_adapter_allowlist.py``. Nothing else imports it.

The allowlist tests hand the adapter loader a spec naming this module
while ``tests`` is NOT on the allowlist, then assert the module never
reached :data:`sys.modules` -- proof the refusal happened before any
import, independent of how the loader imports.
"""

from __future__ import annotations


def make_adapter() -> None:
    """Never reached: the loader must refuse this module before import."""
    raise AssertionError("adapter import canary factory was called")
