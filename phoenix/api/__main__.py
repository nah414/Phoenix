"""Module entry point: `python -m phoenix.api --port 8003` boots the daemon.

Phase 0 supports a single CLI flag, --port, defaulting to 8003 per architecture
v1 Section 5.4. Later phases add --host, --workers, --reload, plus configuration
loading from ~/.phoenix/config.yaml.
"""

from __future__ import annotations

import argparse
import sys

import uvicorn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m phoenix.api",
        description="Phoenix v1 daemon -- Phase 0 skeleton",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8003,
        help="Port to bind on (default 8003 per architecture Section 5.4)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind on (default 127.0.0.1; loopback only in Phase 0)",
    )
    args = parser.parse_args(argv)

    uvicorn.run(
        "phoenix.api.routes:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
