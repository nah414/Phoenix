"""Phase 12 distribution acceptance battery.

Tests under this directory are marked ``@pytest.mark.distribution`` and
build the three v1 release artifacts (pip wheel, Docker image, Nuitka
standalone binary). They're slow + expensive (full build per test);
the default ``pytest tests/`` sweep deselects them via the marker.

Run locally with:

    pytest tests/distribution -v -m distribution

Run in CI: the release workflow at ``.github/workflows/release.yml``
exercises these implicitly by building + smoke-testing each artifact.
"""
