# Phoenix v1 runtime topology

What runs when you boot Phoenix, where the configuration lives, what
ports + paths are involved, and how to escape the bundled-daemon
default when you need to.

## The two-process model

Per architecture v1 Section 1 Decision 33, a solo Phoenix install boots
**two processes** under a single launcher:

1. **NATS JetStream** -- the durable queue + event broker. Phoenix
   publishes task ingress + per-task events here; consumers (the
   Trinity Core worker, the WebSocket fan-out, the audit hook) read
   from this same broker.
2. **Phoenix daemon** -- the FastAPI + uvicorn web server. Hosts the
   REST surface (`/v1/tasks`, `/v1/admin/*`, ...), the WebSocket
   surface (`/v1/ws/tasks/*`, `/v1/ws/calibration/drift`), and the
   Trinity Core orchestration layer behind them.

```
launcher (phoenix / python -m phoenix / phoenix-windows-x64.exe)
   |
   +-- spawns: nats-server --jetstream (port 4222, monitor 8222)
   |
   +-- spawns: python -m phoenix.api (port 8003)
   |
   +-- health-probes Phoenix at GET /v1/health
   |
   +-- opens browser at http://127.0.0.1:8003/docs (default)
   |
   +-- traps Ctrl+C; terminates both children with 5s grace
```

## Ports

| Port | Process | Protocol | Default bind |
|---|---|---|---|
| 8003 | Phoenix daemon | HTTP + WebSocket | `127.0.0.1` (pip + standalone), `0.0.0.0` (Docker) |
| 4222 | NATS JetStream | nats:// | `127.0.0.1` (pip + standalone), `0.0.0.0` (Docker) |
| 8222 | NATS monitoring | HTTP | `127.0.0.1` (pip + standalone), `0.0.0.0` (Docker) |

Override the daemon port via `--port` or `$PHOENIX_PORT`:

```bash
phoenix --port 9999
```

Override the NATS port via `--nats-port` or `$NATS_PORT`:

```bash
phoenix --nats-port 14222
```

## Paths

Per-user state lives under `~/.phoenix/` on Unix-style installs (Linux,
macOS, and Windows-via-Cygwin) and `%USERPROFILE%\.phoenix\` on native
Windows. The Docker image stores at `/home/phoenix/.phoenix/`.

| Subdir | Owner | Purpose |
|---|---|---|
| `state/` | SQLite (default) or Postgres adapter | actor permissions, kill switch, drift state, audit pointers |
| `identity/` | Ed25519 keystore | actor private keys (file mode 0600) |
| `audit/` | JSONL writer | append-only audit log files |
| `runtime/nats/` | nats-server | JetStream file storage |

## `--external-daemon` and `--external-nats`

Per Section 11.3.3 RESOLVED, the launcher bundles the daemon by
default; the `--external-daemon` flag is the opt-out for sysadmins
running Phoenix's daemon under systemd / nssm / docker-compose
separately.

```bash
# Sysadmin: Phoenix's daemon runs under systemd; launcher just opens docs.
phoenix --external-daemon

# Phoenix Cloud (hypothetical): NATS is a managed service.
phoenix --external-nats
# launcher spawns the daemon but skips NATS bootstrap.

# Both external: launcher becomes a no-op browser opener.
phoenix --external-daemon --external-nats
```

When `--external-daemon` is set, the launcher still health-probes the
daemon at `http://<host>:<port>/v1/health` before opening the docs URL.
If the probe fails within 30 seconds, the launcher exits 3
(`EXIT_DAEMON_UNREACHABLE`).

When `--external-nats` is set, the launcher skips the NATS bootstrap
entirely. Phoenix's queue module reads `$PHOENIX_NATS_URL` (default
`nats://127.0.0.1:4222`); set this to your external NATS instance.

## Healthcheck endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1/health` | GET | Liveness + version string |
| `/v1/admin/health/detailed` | GET | Full health report (requires admin auth) |
| `/v1/admin/governor` | GET | System-resource snapshot |
| `/v1/admin/calibration/detail` | GET | Calibration baseline + current drift |

Docker / Kubernetes health probes should hit `/v1/health` -- it's
unauthenticated and answers in <50ms.

## Log locations

The pip wheel + standalone binary log to stdout / stderr (the launcher
inherits them; the daemon's uvicorn logs flow through the launcher's
terminal).

The Docker image's logs go to the container's stdout/stderr; capture
via `docker logs phoenix` or your container runtime's log driver.

If you need persistent logs, point uvicorn at a file via:

```bash
phoenix > phoenix.log 2>&1 &
```

A v1.1 enhancement may ship a `--log-file` flag on the launcher.

## Configuration files

Phoenix reads `~/.phoenix/config.yaml` at boot if present. CLI flags
override config-file values; environment variables override CLI flags;
config-file values are the lowest-priority defaults.

```yaml
# ~/.phoenix/config.yaml
rest_url: "http://localhost:8003"
reproducibility_mode: "permissive"   # | "strict" | "replay"
default_actor: "bootstrap"
output_format: "auto"
```

## Stopping Phoenix

The launcher traps `SIGINT` (Ctrl+C) and `SIGTERM` (`kill <pid>`) and
gracefully terminates both children with a 5-second grace period
before escalating to `SIGKILL`.

On Windows, the launcher uses `CREATE_NEW_PROCESS_GROUP` for each
child so Ctrl+Break can propagate cleanly without killing the
launcher itself.

For Docker:

```bash
docker stop phoenix       # sends SIGTERM, then SIGKILL after 10s
```

For systemd-managed installs, the standard `systemctl stop phoenix`
works -- the daemon's signal handlers do the same shutdown dance.
