"""Module entry: ``python -m phoenix`` invokes the launcher.

Phase 12 Step 2 -- the launcher orchestrates the two-process model
(Phoenix daemon + NATS JetStream) per Section 1 Decision 33. See
:mod:`phoenix.launcher` for the CLI surface.

The daemon-only entry stays at :mod:`phoenix.api.__main__`
(``python -m phoenix.api``) so the existing launch.bat / launch.sh
scripts continue to boot the daemon directly without the launcher's
orchestration layer.
"""

from __future__ import annotations

import sys

from phoenix.launcher import main

if __name__ == "__main__":
    sys.exit(main())
