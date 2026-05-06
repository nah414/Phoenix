#!/usr/bin/env bash
# Phoenix v1 launcher -- Phase 0 (macOS/Linux)
# Boots the empty Phoenix daemon (FastAPI on port 8003).
# NATS JetStream and vendor-integrity check land in later phases.

set -euo pipefail

PHOENIX_HOME="${PHOENIX_HOME:-$HOME/Phoenix}"
PHOENIX_PORT="${PHOENIX_PORT:-8003}"

if [ ! -f "$PHOENIX_HOME/pyproject.toml" ]; then
    echo "ERROR: $PHOENIX_HOME does not look like a Phoenix install." >&2
    echo "Expected pyproject.toml at $PHOENIX_HOME/pyproject.toml" >&2
    exit 1
fi

cd "$PHOENIX_HOME"

echo "Booting Phoenix daemon on port $PHOENIX_PORT..."
python -m phoenix.api --port "$PHOENIX_PORT" &
DAEMON_PID=$!

# Give the daemon a moment to start.
sleep 2

# Open docs in the platform's default browser.
case "$(uname -s)" in
    Darwin) open "http://localhost:$PHOENIX_PORT/docs" ;;
    Linux)  xdg-open "http://localhost:$PHOENIX_PORT/docs" 2>/dev/null || true ;;
esac

echo "Phoenix v1 (Phase 0) running (daemon PID $DAEMON_PID). Press Ctrl+C to stop."

# Wait for the daemon and clean up on Ctrl+C.
trap "kill $DAEMON_PID 2>/dev/null || true; exit 0" INT TERM
wait $DAEMON_PID
