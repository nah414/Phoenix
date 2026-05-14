#!/usr/bin/env bash
# Phoenix v1 launcher with embedded NATS -- Phase 12 Step 6 (Linux/macOS).
#
# Per architecture Decision 33: a solo Phoenix install boots two
# processes -- Phoenix itself + NATS -- managed by the same launcher.
# This script demonstrates the two-process model: starts NATS with
# JetStream file-backed storage, then starts the Phoenix daemon.
#
# Mirrors scripts/launch_with_nats.bat (Windows). Phase 12 lands the
# Linux equivalent as part of the distribution-artifact build-out.
#
# Prerequisites:
#   - nats-server installed (see https://docs.nats.io/running-a-nats-service/introduction/installation)
#       On Debian/Ubuntu, build from source or use the static binary
#       from the GitHub release. The Docker image bundles nats-server
#       automatically; this script is for native installs.
#   - Phoenix-middleware[nats] installed (pip install -e .[nats] or
#       pip install 'phoenix-middleware[nats]')
#
# Usage:
#   ./scripts/launch_with_nats.sh
#   PHOENIX_PORT=9999 ./scripts/launch_with_nats.sh

set -euo pipefail

PHOENIX_HOME="${PHOENIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PHOENIX_PORT="${PHOENIX_PORT:-8003}"
NATS_PORT="${NATS_PORT:-4222}"
NATS_MONITOR_PORT="${NATS_MONITOR_PORT:-8222}"
NATS_STORE_DIR="${NATS_STORE_DIR:-$HOME/.phoenix/runtime/nats}"

if [ ! -f "$PHOENIX_HOME/pyproject.toml" ]; then
    echo "ERROR: $PHOENIX_HOME does not look like a Phoenix install." >&2
    echo "Expected pyproject.toml at $PHOENIX_HOME/pyproject.toml" >&2
    exit 1
fi

if ! command -v nats-server >/dev/null 2>&1; then
    echo "ERROR: nats-server not found on PATH." >&2
    echo "Install via your package manager or grab the static binary from:" >&2
    echo "  https://github.com/nats-io/nats-server/releases/latest" >&2
    echo "Or use the Phoenix Docker image, which bundles nats-server." >&2
    exit 1
fi

mkdir -p "$NATS_STORE_DIR"

cd "$PHOENIX_HOME"

# Track child PIDs so the shutdown trap can clean up cleanly.
NATS_PID=""
DAEMON_PID=""

shutdown() {
    echo
    echo "phoenix launcher: stopping children..."
    if [ -n "$DAEMON_PID" ] && kill -0 "$DAEMON_PID" 2>/dev/null; then
        kill -TERM "$DAEMON_PID" 2>/dev/null || true
        # Give Phoenix up to 5s to exit gracefully, then SIGKILL.
        for _ in 1 2 3 4 5; do
            kill -0 "$DAEMON_PID" 2>/dev/null || break
            sleep 1
        done
        kill -KILL "$DAEMON_PID" 2>/dev/null || true
    fi
    if [ -n "$NATS_PID" ] && kill -0 "$NATS_PID" 2>/dev/null; then
        kill -TERM "$NATS_PID" 2>/dev/null || true
        for _ in 1 2 3 4 5; do
            kill -0 "$NATS_PID" 2>/dev/null || break
            sleep 1
        done
        kill -KILL "$NATS_PID" 2>/dev/null || true
    fi
    exit 0
}
trap shutdown INT TERM

echo "phoenix launcher: starting NATS (port $NATS_PORT, monitor $NATS_MONITOR_PORT, store $NATS_STORE_DIR)..."
nats-server \
    --jetstream \
    --store_dir "$NATS_STORE_DIR" \
    --port "$NATS_PORT" \
    --http_port "$NATS_MONITOR_PORT" \
    &
NATS_PID=$!

# Brief sleep so NATS binds before the daemon's queue module connects.
# Production should poll the monitoring port; this script keeps it simple.
sleep 3

echo "phoenix launcher: starting Phoenix daemon on port $PHOENIX_PORT..."
export PHOENIX_NATS_URL="nats://127.0.0.1:$NATS_PORT"
python -m phoenix.api --port "$PHOENIX_PORT" &
DAEMON_PID=$!

echo "phoenix launcher: Phoenix v1 running. Press Ctrl+C to stop."

# Wait on the daemon; the shutdown trap handles cleanup on Ctrl+C.
wait "$DAEMON_PID"

# If we reach here, the daemon exited on its own; shut down NATS too.
shutdown
