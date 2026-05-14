"""CLI config loader (Phase 9 Step 6).

Per architecture v1 Section 5.4: the Phoenix CLI reads its
configuration from ``~/.phoenix/config.yaml`` (per-user defaults)
with environment variables taking precedence. The two recognised
env-var overrides today are:

- ``$PHOENIX_REST_URL`` -- base URL of the Phoenix daemon
  (e.g., ``http://localhost:8000``).
- ``$PHOENIX_REPRODUCIBILITY_MODE`` -- one of ``permissive`` |
  ``strict`` | ``replay`` (Phase 7 Section 1 Decision 19).

The loader is robust:

- ``~/.phoenix/config.yaml`` missing -> defaults.
- YAML present but malformed -> :class:`ConfigError`.
- Unknown keys in the YAML -> ignored (forward-compat with later
  CLI surfaces).

The returned :class:`CLIConfig` is a frozen dataclass so command
handlers can pass it around without worrying about mutation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

_DEFAULT_REST_URL = "http://localhost:8000"
_DEFAULT_REPRO_MODE = "permissive"
_VALID_REPRO_MODES = frozenset({"permissive", "strict", "replay"})
_DEFAULT_CONFIG_PATH = Path.home() / ".phoenix" / "config.yaml"


class ConfigError(Exception):
    """Raised when ``config.yaml`` is malformed or contains
    invalid values.
    """


@dataclass(frozen=True)
class CLIConfig:
    """Resolved CLI configuration.

    Fields:
      - ``rest_url`` -- base URL of the Phoenix daemon.
      - ``reproducibility_mode`` -- ``permissive`` | ``strict`` |
        ``replay``.
      - ``default_actor`` -- actor name to use when no
        ``--actor`` flag is passed. ``None`` means "use bootstrap"
        (the dev-mode default).
      - ``output_format`` -- default render mode (``json`` |
        ``text`` | ``table`` | ``auto``). ``auto`` lets
        :mod:`phoenix.cli.output_formats` pick based on TTY.
      - ``raw`` -- the full parsed YAML dict for forward-compat;
        Step 7+ handlers can read keys the dataclass doesn't yet
        surface.
    """

    rest_url: str = _DEFAULT_REST_URL
    reproducibility_mode: str = _DEFAULT_REPRO_MODE
    default_actor: str | None = None
    output_format: str = "auto"
    raw: dict[str, Any] = field(default_factory=dict)


def load_config(
    *,
    config_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> CLIConfig:
    """Load + validate the CLI config.

    Parameters:
      - ``config_path``: explicit YAML path (tests pass a fixture);
        defaults to ``~/.phoenix/config.yaml``.
      - ``env``: explicit env dict (tests inject); defaults to
        ``os.environ``.

    Resolution order (later wins):
      1. dataclass defaults
      2. ``config.yaml`` contents
      3. env-var overrides

    Raises :class:`ConfigError` on malformed YAML or invalid
    ``reproducibility_mode`` values.
    """
    if env is None:
        env = dict(os.environ)
    path = config_path if config_path is not None else _DEFAULT_CONFIG_PATH

    raw: dict[str, Any] = {}
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                parsed = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise ConfigError(f"Malformed YAML at {path}: {exc}") from exc
        if parsed is None:
            raw = {}
        elif isinstance(parsed, dict):
            raw = parsed
        else:
            raise ConfigError(
                f"Config at {path} must be a YAML mapping (got {type(parsed).__name__})"
            )

    rest_url = str(raw.get("rest_url") or _DEFAULT_REST_URL)
    reproducibility_mode = str(raw.get("reproducibility_mode") or _DEFAULT_REPRO_MODE)
    default_actor_raw = raw.get("default_actor")
    default_actor: str | None = str(default_actor_raw) if default_actor_raw else None
    output_format = str(raw.get("output_format") or "auto")

    # Env-var overrides take precedence over file contents.
    if env.get("PHOENIX_REST_URL"):
        rest_url = env["PHOENIX_REST_URL"]
    if env.get("PHOENIX_REPRODUCIBILITY_MODE"):
        reproducibility_mode = env["PHOENIX_REPRODUCIBILITY_MODE"]

    if reproducibility_mode not in _VALID_REPRO_MODES:
        raise ConfigError(
            f"Invalid reproducibility_mode {reproducibility_mode!r}; "
            f"must be one of {sorted(_VALID_REPRO_MODES)}"
        )

    return CLIConfig(
        rest_url=rest_url.rstrip("/"),
        reproducibility_mode=reproducibility_mode,
        default_actor=default_actor,
        output_format=output_format,
        raw=raw,
    )


__all__ = ["CLIConfig", "ConfigError", "load_config"]
