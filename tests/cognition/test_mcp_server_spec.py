"""Tests for ``MCPServerSpec`` construction-time validation.

These tests enforce 13-D4's load-bearing discipline:
- '*' in allowed_tools is forbidden
- empty allowed_tools is forbidden
- empty name / endpoint is forbidden
- invalid transport is forbidden
- non-positive budget is forbidden
- auth_config never appears in repr() or to_dict()
"""

from __future__ import annotations

import pytest

from phoenix.mcp.errors import MCPRegistrationError
from phoenix.mcp.server_spec import MCPServerSpec


def _valid_spec(**overrides):
    base = {
        "name": "test",
        "transport": "stdio",
        "endpoint": "test-server",
        "allowed_tools": ("read_file",),
        "auth_config": {},
    }
    base.update(overrides)
    return MCPServerSpec(**base)


def test_valid_spec_constructs() -> None:
    spec = _valid_spec()
    assert spec.name == "test"
    assert spec.transport == "stdio"
    assert spec.allowed_tools == ("read_file",)


def test_wildcard_allowed_tools_forbidden() -> None:
    """13-D4: '*' in allowed_tools is the load-bearing rejection."""
    with pytest.raises(MCPRegistrationError, match=r"\*"):
        _valid_spec(allowed_tools=("*",))


def test_wildcard_mixed_with_other_tools_forbidden() -> None:
    """Even when other tools are listed, '*' inclusion fails."""
    with pytest.raises(MCPRegistrationError, match=r"\*"):
        _valid_spec(allowed_tools=("read_file", "*"))


def test_empty_allowed_tools_forbidden() -> None:
    """An empty allowlist disables all tools; explicit de-registration is the path."""
    with pytest.raises(MCPRegistrationError, match="cannot be empty"):
        _valid_spec(allowed_tools=())


def test_empty_name_forbidden() -> None:
    with pytest.raises(MCPRegistrationError, match="name cannot be empty"):
        _valid_spec(name="")


def test_empty_endpoint_forbidden() -> None:
    with pytest.raises(MCPRegistrationError, match="endpoint cannot be empty"):
        _valid_spec(endpoint="")


def test_invalid_transport_forbidden() -> None:
    with pytest.raises(MCPRegistrationError, match="transport"):
        _valid_spec(transport="websocket")


def test_negative_budget_forbidden() -> None:
    with pytest.raises(MCPRegistrationError, match="max_budget"):
        _valid_spec(max_budget_usd_per_day=-1.0)


def test_zero_budget_forbidden() -> None:
    with pytest.raises(MCPRegistrationError, match="max_budget"):
        _valid_spec(max_budget_usd_per_day=0.0)


def test_invalid_audit_policy_forbidden() -> None:
    with pytest.raises(MCPRegistrationError, match="audit_export_policy"):
        _valid_spec(audit_export_policy="banana")


def test_repr_redacts_auth_config() -> None:
    """auth_config must never appear in repr — SAFETY contract."""
    spec = _valid_spec(auth_config={"secret_token": "DO_NOT_LEAK"})
    r = repr(spec)
    assert "DO_NOT_LEAK" not in r
    assert "secret_token" not in r
    assert "<redacted>" in r


def test_to_dict_redacts_auth_config() -> None:
    """to_dict (JSON persistence) must never include auth_config cleartext."""
    spec = _valid_spec(auth_config={"bearer": "SECRET_TOKEN"})
    payload = spec.to_dict()
    assert "SECRET_TOKEN" not in str(payload)
    assert payload["auth_config"] == "<redacted>"


def test_tool_allowed_exact_match() -> None:
    """Exact-match only per [OPEN: P13-5] Step 6 default."""
    spec = _valid_spec(allowed_tools=("read_file", "write_file"))
    assert spec.tool_allowed("read_file") is True
    assert spec.tool_allowed("write_file") is True
    assert spec.tool_allowed("delete_file") is False
    assert spec.tool_allowed("read_") is False  # no prefix match
    assert spec.tool_allowed("read_file_v2") is False  # no glob


def test_from_dict_roundtrip() -> None:
    """to_dict → from_dict reconstructs the spec (with auth re-supplied)."""
    original = _valid_spec(
        allowed_tools=("a", "b"),
        max_budget_usd_per_day=25.0,
        audit_export_policy="hashes_only",
    )
    payload = original.to_dict()
    rebuilt = MCPServerSpec.from_dict(payload, auth_config={"new": "auth"})
    assert rebuilt.name == original.name
    assert rebuilt.transport == original.transport
    assert rebuilt.endpoint == original.endpoint
    assert rebuilt.allowed_tools == original.allowed_tools
    assert rebuilt.max_budget_usd_per_day == 25.0
    assert rebuilt.audit_export_policy == "hashes_only"
    assert rebuilt.auth_config == {"new": "auth"}
