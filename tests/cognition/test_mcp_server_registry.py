"""Tests for ``MCPServerRegistry`` round-trip + JSON persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from phoenix.mcp.errors import MCPRegistrationError
from phoenix.mcp.server_registry import MCPServerRegistry
from phoenix.mcp.server_spec import MCPServerSpec


def _spec(name: str = "test", *, allowed_tools: tuple[str, ...] = ("read_file",)) -> MCPServerSpec:
    return MCPServerSpec(
        name=name,
        transport="stdio",
        endpoint=f"{name}-server",
        allowed_tools=allowed_tools,
        auth_config={"secret": "DO_NOT_PERSIST"},
    )


def test_fresh_registry_empty(tmp_path: Path) -> None:
    """Default state: zero entries. 13-D4's load-bearing invariant."""
    reg = MCPServerRegistry(persistence_path=tmp_path / "mcp.json")
    assert len(reg) == 0
    assert reg.list_servers() == []
    assert reg.get("anything") is None
    assert "anything" not in reg


def test_register_and_lookup(tmp_path: Path) -> None:
    reg = MCPServerRegistry(persistence_path=tmp_path / "mcp.json")
    spec = _spec("github")
    reg.register(spec)
    assert len(reg) == 1
    assert reg.get("github") is spec
    assert "github" in reg
    assert reg.list_servers() == [spec]


def test_register_replaces_existing(tmp_path: Path) -> None:
    """Idempotent on name: re-register replaces."""
    reg = MCPServerRegistry(persistence_path=tmp_path / "mcp.json")
    reg.register(_spec("github", allowed_tools=("a",)))
    reg.register(_spec("github", allowed_tools=("a", "b")))
    spec = reg.get("github")
    assert spec is not None
    assert spec.allowed_tools == ("a", "b")
    assert len(reg) == 1


def test_unregister_removes(tmp_path: Path) -> None:
    reg = MCPServerRegistry(persistence_path=tmp_path / "mcp.json")
    reg.register(_spec("github"))
    reg.unregister("github")
    assert len(reg) == 0
    assert reg.get("github") is None


def test_unregister_missing_is_idempotent(tmp_path: Path) -> None:
    """De-registering a missing name doesn't raise."""
    reg = MCPServerRegistry(persistence_path=tmp_path / "mcp.json")
    reg.unregister("never-registered")  # no error
    assert len(reg) == 0


def test_persistence_round_trip(tmp_path: Path) -> None:
    """Register → write → reload from disk → entry is there (sans auth)."""
    persist_path = tmp_path / "mcp.json"

    reg_a = MCPServerRegistry(persistence_path=persist_path)
    reg_a.register(_spec("github"))
    reg_a.register(_spec("postgres", allowed_tools=("query", "schema")))

    # Build a fresh registry pointing at the same path.
    reg_b = MCPServerRegistry(persistence_path=persist_path)
    reg_b.load_from_disk()

    assert len(reg_b) == 2
    assert reg_b.get("github") is not None
    assert reg_b.get("postgres") is not None
    assert reg_b.get("postgres").allowed_tools == ("query", "schema")  # type: ignore[union-attr]


def test_persistence_does_not_leak_auth(tmp_path: Path) -> None:
    """SAFETY: auth_config never written to disk in cleartext."""
    persist_path = tmp_path / "mcp.json"
    reg = MCPServerRegistry(persistence_path=persist_path)
    reg.register(_spec("api", allowed_tools=("call",)))
    # The spec carries auth_config={"secret": "DO_NOT_PERSIST"}; verify it
    # doesn't appear in the persisted JSON.
    file_contents = persist_path.read_text(encoding="utf-8")
    assert "DO_NOT_PERSIST" not in file_contents


def test_persistence_load_clears_auth(tmp_path: Path) -> None:
    """SAFETY: reloaded specs have empty auth_config (auth must be re-supplied)."""
    persist_path = tmp_path / "mcp.json"
    reg_a = MCPServerRegistry(persistence_path=persist_path)
    reg_a.register(_spec("api"))

    reg_b = MCPServerRegistry(persistence_path=persist_path)
    reg_b.load_from_disk()
    spec = reg_b.get("api")
    assert spec is not None
    assert spec.auth_config == {}  # auth wiped on reload


def test_load_from_disk_missing_file_is_empty(tmp_path: Path) -> None:
    """Missing persistence file → empty registry, no error."""
    reg = MCPServerRegistry(persistence_path=tmp_path / "does-not-exist.json")
    reg.load_from_disk()
    assert len(reg) == 0


def test_load_from_disk_malformed_raises(tmp_path: Path) -> None:
    """Malformed JSON → MCPRegistrationError (loud failure)."""
    persist_path = tmp_path / "bad.json"
    persist_path.write_text("{not valid json", encoding="utf-8")
    reg = MCPServerRegistry(persistence_path=persist_path)
    with pytest.raises(MCPRegistrationError):
        reg.load_from_disk()


def test_registry_atomic_write(tmp_path: Path) -> None:
    """Atomic-write: no .tmp file left behind after successful registration."""
    persist_path = tmp_path / "mcp.json"
    reg = MCPServerRegistry(persistence_path=persist_path)
    reg.register(_spec("test"))
    assert persist_path.exists()
    assert not persist_path.with_suffix(".json.tmp").exists()


def test_registry_persists_immediately_on_register(tmp_path: Path) -> None:
    """Registration writes to disk synchronously."""
    persist_path = tmp_path / "mcp.json"
    reg = MCPServerRegistry(persistence_path=persist_path)
    assert not persist_path.exists()
    reg.register(_spec("test"))
    assert persist_path.exists()
