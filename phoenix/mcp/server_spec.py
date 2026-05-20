"""``MCPServerSpec`` — frozen dataclass for one registered MCP server.

Per Phase 13 build guide Step 6 + 13-D4 (locked): the spec carries
everything the dispatch path needs to enforce 13-D4's per-server
explicit-allowlist contract. The construction-time validation
forbids ``["*"]`` in ``allowed_tools`` — that's the load-bearing
discipline of 13-D4.

**SAFETY:** :attr:`auth_config` is an opaque dict whose contents
must NEVER be logged or serialized into audit entries. The
:class:`MCPServerSpec.__repr__` deliberately redacts the auth field;
the JSON serialization (via :meth:`to_dict`) writes only a
constant placeholder for auth_config. The registry persistence layer
encrypts auth_config at rest (Step 6 Phase 13 follow-up; Step 6 ships
the field with a documented redaction contract and a clear path for
encryption to land).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from phoenix.mcp.errors import MCPRegistrationError

# Transport types per Phase 13 build guide §4.6: stdio (process pipe)
# and http+sse (server-sent events over HTTP).
MCPTransport = Literal["stdio", "http+sse"]

# Per Phase 10 cost-ceiling integration: every server gets a daily
# budget cap. The default is conservative; ops bump as needed.
_DEFAULT_BUDGET_USD_PER_DAY: float = 10.0

# Audit-export policy values per build guide §4.6.
AuditExportPolicy = Literal["full", "hashes_only"]

# Per-server prompt_disposition override (None = inherit per-task
# setting). Mirrors the 13-D2 prompt_disposition enum values.
PromptDispositionOverride = Literal["HASH_ONLY", "VERBATIM"] | None


@dataclass(frozen=True)
class MCPServerSpec:
    """One registered MCP server's full configuration.

    Frozen: registration mutations replace the spec entry in the
    registry rather than mutating an existing instance. This matches
    the Phase 6a ``ActorPermissions`` pattern.

    Fields:
        name: The registered name (e.g. ``"github"``,
            ``"customer-vpc-llama"``). Used in dispatch as the lookup
            key.
        transport: Either ``"stdio"`` or ``"http+sse"``.
        endpoint: The transport-specific endpoint:
            - stdio: the command + args to spawn (e.g.
              ``"mcp-server-github"``).
            - http+sse: the URL (e.g.
              ``"https://customer-mcp.example.com/sse"``).
        auth_config: Provider-specific auth config (opaque dict). For
            stdio this might be environment variables; for http+sse
            this might be a bearer-token shape. **Never logged.**
        allowed_tools: Explicit list of allowed tool names. ``["*"]``
            is forbidden at registration time; tool access requires
            explicit per-tool listing.
        max_budget_usd_per_day: Hard ceiling on per-server daily
            spend. The cost-ceiling engine (Phase 10) accumulates
            spend in a 24h sliding window; budget exceeded → 429
            with ``Retry-After`` header.
        audit_export_policy: ``"full"`` (full per-call audit records)
            or ``"hashes_only"`` (privacy-aware; only hashes of
            arguments + responses).
        prompt_disposition_override: When the MCP server is used as
            a cognition provider, override the per-task
            prompt_disposition setting. ``None`` means "inherit per
            task".
    """

    name: str
    transport: MCPTransport
    endpoint: str
    allowed_tools: tuple[str, ...]
    auth_config: dict[str, Any] = field(default_factory=dict)
    max_budget_usd_per_day: float = _DEFAULT_BUDGET_USD_PER_DAY
    audit_export_policy: AuditExportPolicy = "full"
    prompt_disposition_override: PromptDispositionOverride = None

    def __post_init__(self) -> None:
        """Construction-time validation.

        Enforces 13-D4's load-bearing discipline: ``["*"]`` in
        ``allowed_tools`` is forbidden; tool listing must be explicit.
        """
        if not self.name:
            raise MCPRegistrationError("MCPServerSpec.name cannot be empty.")
        if not self.endpoint:
            raise MCPRegistrationError(f"MCPServerSpec({self.name!r}).endpoint cannot be empty.")
        if self.transport not in ("stdio", "http+sse"):
            raise MCPRegistrationError(
                f"MCPServerSpec({self.name!r}).transport must be 'stdio' or "
                f"'http+sse'; got {self.transport!r}."
            )
        if "*" in self.allowed_tools:
            raise MCPRegistrationError(
                f"MCPServerSpec({self.name!r}).allowed_tools cannot contain "
                f"'*' per 13-D4. Tool listing must be explicit; list each "
                f"tool name."
            )
        if not self.allowed_tools:
            raise MCPRegistrationError(
                f"MCPServerSpec({self.name!r}).allowed_tools cannot be empty. "
                f"An empty allowlist disables all tools; if that's the intent, "
                f"de-register the server instead via DELETE."
            )
        if self.max_budget_usd_per_day <= 0:
            raise MCPRegistrationError(
                f"MCPServerSpec({self.name!r}).max_budget_usd_per_day must "
                f"be > 0; got {self.max_budget_usd_per_day}."
            )
        if self.audit_export_policy not in ("full", "hashes_only"):
            raise MCPRegistrationError(
                f"MCPServerSpec({self.name!r}).audit_export_policy must be "
                f"'full' or 'hashes_only'; got {self.audit_export_policy!r}."
            )

    def __repr__(self) -> str:
        """Redacted repr — auth_config keys/values never appear."""
        return (
            f"MCPServerSpec(name={self.name!r}, transport={self.transport!r}, "
            f"endpoint={self.endpoint!r}, "
            f"allowed_tools={self.allowed_tools!r}, "
            f"max_budget_usd_per_day={self.max_budget_usd_per_day}, "
            f"audit_export_policy={self.audit_export_policy!r}, "
            f"prompt_disposition_override={self.prompt_disposition_override!r}, "
            f"auth_config=<redacted>)"
        )

    def tool_allowed(self, tool_name: str) -> bool:
        """Return ``True`` iff ``tool_name`` is in the explicit allowlist.

        Exact-match only per 13-D4 + ``[OPEN: P13-5]`` Step 6 default.
        Glob support deferred to v1.2 behind a
        ``can_register_glob_pattern`` permission.
        """
        return tool_name in self.allowed_tools

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON persistence.

        Per SAFETY contract: ``auth_config`` is written as a constant
        redacted placeholder; the actual auth values must be supplied
        again at re-registration (or, when encryption-at-rest lands,
        loaded from the encrypted column).
        """
        return {
            "name": self.name,
            "transport": self.transport,
            "endpoint": self.endpoint,
            "allowed_tools": list(self.allowed_tools),
            "max_budget_usd_per_day": self.max_budget_usd_per_day,
            "audit_export_policy": self.audit_export_policy,
            "prompt_disposition_override": self.prompt_disposition_override,
            "auth_config": "<redacted>",
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, auth_config: dict[str, Any]) -> MCPServerSpec:
        """Reconstruct from JSON persistence + a separately-supplied auth_config.

        The persistence layer doesn't store auth_config in cleartext
        (per SAFETY). Re-registration must supply auth_config explicitly
        via the admin endpoint body; this method is the integration
        point between the persisted spec and the supplied auth.
        """
        return cls(
            name=data["name"],
            transport=data["transport"],
            endpoint=data["endpoint"],
            allowed_tools=tuple(data["allowed_tools"]),
            auth_config=auth_config,
            max_budget_usd_per_day=float(data["max_budget_usd_per_day"]),
            audit_export_policy=data.get("audit_export_policy", "full"),
            prompt_disposition_override=data.get("prompt_disposition_override"),
        )
