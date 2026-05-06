"""Phase 0 smoke test -- assert the package imports and exposes its version.

This is the single guard that the Phase 0 skeleton compiles. Every later phase
adds real tests; this one stays as the baseline that breaks first if a Phase
1+ change accidentally breaks the basic import path.
"""

from __future__ import annotations


def test_phoenix_imports() -> None:
    import phoenix

    assert hasattr(phoenix, "__version__")
    assert phoenix.__version__ == "1.0.0.dev0"


def test_internal_version_module() -> None:
    from phoenix._internal.version import __version__, read_vendor_version

    assert __version__ == "1.0.0.dev0"
    # Vendor manifest is populated after Phase 1 Step 3 (vendor sync ran).
    # Phase 0 placeholder had empty hash fields; from Phase 1 forward, the
    # vendor sync writes real values for vendor_synced_at, dr_frank_and_eddy_commit,
    # and (after Step 5) calibration_profile_hash.
    vendor = read_vendor_version()
    assert vendor is not None
    assert vendor["phoenix_release"], "phoenix_release must be set"
    assert vendor["vendor_synced_at"], "vendor_synced_at must be set after vendor sync runs"
    assert vendor[
        "dr_frank_and_eddy_commit"
    ], "dr_frank_and_eddy_commit must be set after vendor sync runs"
    assert vendor[
        "calibration_profile_hash"
    ], "calibration_profile_hash must be set after Phase 1 Step 5 calibration generation"


def test_vendored_imports_resolve_from_vendor_dir() -> None:
    """Phase 1 Step 4: sys.path injection in phoenix/__init__.py makes vendored
    modules importable using their upstream paths (architecture v1 Section 11.7.1).
    """
    import phoenix  # noqa: F401  -- triggers sys.path injection on package load

    # Vendored modules should resolve at their upstream import paths.
    import actor.actor as vendored_actor
    import grammar.parser as vendored_parser
    import synthesis.equations.base as vendored_solver_base
    import wobble.disagreement_types as vendored_wobble_types

    # Each module's __file__ should live under vendor/.
    for module in (vendored_actor, vendored_parser, vendored_solver_base, vendored_wobble_types):
        assert module.__file__ is not None
        assert "vendor" in module.__file__.replace("\\", "/").split(
            "/"
        ), f"{module.__name__} resolved from {module.__file__} -- expected under vendor/"


def test_all_section_subpackages_import() -> None:
    """Every directory listed in architecture v1 Section 10.3 imports as a submodule."""
    import phoenix.adapters  # noqa: F401
    import phoenix.admin  # noqa: F401
    import phoenix.api  # noqa: F401
    import phoenix.audit  # noqa: F401
    import phoenix.cli  # noqa: F401
    import phoenix.grammar  # noqa: F401
    import phoenix.identity  # noqa: F401
    import phoenix.ledger  # noqa: F401
    import phoenix.mcp  # noqa: F401
    import phoenix.providers  # noqa: F401
    import phoenix.providers.classical  # noqa: F401
    import phoenix.providers.cloud_gpu  # noqa: F401
    import phoenix.providers.cognition  # noqa: F401
    import phoenix.providers.quantum  # noqa: F401
    import phoenix.queue as q  # `queue` is a stdlib module -- verify shadowing inside namespace
    import phoenix.router  # noqa: F401
    import phoenix.safety  # noqa: F401
    import phoenix.state  # noqa: F401
    import phoenix.trinity  # noqa: F401
    import phoenix.trinity.control  # noqa: F401
    import phoenix.trinity.orchestrate  # noqa: F401
    import phoenix.trinity.solver  # noqa: F401
    import phoenix.verification  # noqa: F401

    assert q.__name__ == "phoenix.queue"
