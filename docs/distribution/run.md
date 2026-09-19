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
| 4222 | NATS JetStream | nats:// | `127.0.0.1` everywhere (the launcher passes `--addr 127.0.0.1`; NATS has no authentication, and the daemon only connects on loopback) |
| 8222 | NATS monitoring | HTTP | `127.0.0.1` everywhere (follows the NATS bind) |

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
| `/v1/admin/health/detailed` | GET | Full health report (requires a signed admin actor) |
| `/v1/admin/governor` | GET | System-resource snapshot (signed admin actor) |
| `/v1/admin/calibration/detail` | GET | Calibration baseline + current drift (signed admin actor) |

Docker / Kubernetes health probes should hit `/v1/health` -- it's
unauthenticated and answers in <50ms.

## Authentication

Every HTTP route except `/v1/health`, the OpenAPI docs and the static cognition
page requires a signed actor:

```
Authorization: Phoenix-Actor <base64 JSON of {name, identity_fingerprint, issued_at, signature}>
```

Exceptions:

- **`/v1/cognition/*`** (the cognition control panel's API) also accepts
  `X-Phoenix-UI-Token: <token>` when the daemon was started with `PHOENIX_UI_TOKEN`
  set; when that variable is set the token is always required. The UI token opens
  `/v1/cognition/*` only, never any other route. See
  [STEP5C_MOBILE_UI.md](../planning/STEP5C_MOBILE_UI.md).
- **WebSockets** (`/v1/ws/tasks/{task_id}/stream`, `/v1/ws/calibration/drift`) ignore
  the `Authorization` header. They authenticate with a single-use `token` query
  parameter, valid for 60 seconds, minted by `POST /v1/identity/ws-token`; only that
  mint needs the signed actor (with `can_submit_tasks`). A handshake without a valid
  token is closed with code 1008. Mint with the signed header (see below), then
  connect within 60 s to e.g. `ws://127.0.0.1:8003/v1/ws/tasks/<task_id>/stream?token=<token>`.

The signature is an HMAC over `name|fingerprint|issued_at` with the install
master key (`~/.phoenix/runtime/master_key.bin`, created by the daemon on first
start) and is valid for 5 minutes. A request with no `Authorization` header, an
empty one, or one that fails verification gets **HTTP 401**, whatever address it
comes from. (Before 2026-09-16 a header-less request was silently treated as the
all-privileged `adam`; that fallback is gone and no flag restores it.)

### One-time setup: tell the CLI who you are

The CLI and `phoenix mcp serve` never sign as anyone implicitly. Name the actor
once, as the OS user that owns the install (pip wheel or standalone binary; for
Docker see the Docker bullet below):

```yaml
# ~/.phoenix/config.yaml
rest_url: "http://127.0.0.1:8003"   # the daemon's default address (and the CLI default)
default_actor: "adam"
```

Without `default_actor` (and without `--actor`) requests go out unsigned, and
protected routes answer 401 with a CLI message saying how to configure an actor.
`phoenix identity show` reports the actor, where it came from, and whether
requests to `rest_url` are signed. If the daemon runs on another port, change
`rest_url` to match: the CLI signs for whatever listens at `rest_url`.

How to authenticate, as the OS user that owns the install:

- **CLI / MCP server** -- `phoenix ...` and `phoenix mcp serve` sign every request
  (freshly, per request) as `--actor <name>`, else `default_actor`. `/v1/health`
  (`phoenix health`, the `phoenix_health` MCP tool) is never signed, so probing a
  wrong `rest_url` sends no credential.
- **Remote daemons and `localhost`** -- a signed header can be replayed by whoever
  receives it for its 5-minute window, so `default_actor` is signed only when
  `rest_url`'s host is a loopback IP address (`127.0.0.1` or another `127.x.y.z`,
  `[::1]`). The name `localhost` does not count: it can resolve to `::1` before
  `127.0.0.1` (Windows does this), the daemon listens on `127.0.0.1` only, and any
  local process can listen on `[::1]` at the same port. For `localhost` or any other
  host the CLI prints a refusal (naming the `127.0.0.1` URL for `localhost`) and
  sends the request unsigned; pass `--actor <name>` on that invocation to sign for
  that daemon deliberately.
- **Proxies** -- requests to a loopback IP or `localhost` always connect directly
  and never use a proxy: `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` and the system
  (Windows registry) proxy settings are ignored for them, so a signed header cannot
  reach a proxy on its way to the local daemon. Any other `rest_url` uses the
  environment's proxy settings.
- **curl / scripts** -- `phoenix identity header` prints a fresh header value for
  the configured actor (exit 4 if none is configured):

  ```bash
  curl -H "Authorization: $(phoenix identity header)" http://127.0.0.1:8003/v1/admin/health/detailed
  ```

- **Swagger UI (`/docs`)** -- paste the output of `phoenix identity header` into the
  endpoint's `authorization` field.
- **Docker** -- the install key lives inside the container (in the `phoenix-state`
  volume), so a CLI on the host cannot sign for a containerized daemon: do not set
  `default_actor` on the host for it. Run authenticated commands inside the
  container, naming the actor: `docker exec phoenix phoenix --actor adam ...`, or
  `docker exec phoenix phoenix --actor adam identity header` for a curl header. The
  image sets `PHOENIX_REST_URL=http://127.0.0.1:8003`, so that in-container CLI
  reaches its daemon. `phoenix health` from the host works unsigned against the
  published port (the default `rest_url` matches `-p 8003:8003`).

Signing reads the key; it never creates one. When this machine has no readable key
(the daemon never ran as this OS user, or it runs in Docker), a `default_actor`
request goes out unsigned: `phoenix health` still works, a protected route's 401
names the keystore problem, and `phoenix identity show` reports
`signing: unsigned: this machine cannot sign (install key unavailable)`. An explicit
`--actor` fails before anything is sent. A 401 for a request that *was* signed
points out that the daemon may be using a different key (a container, another OS
user, another host). A client on another machine has no key and gets 401 on
protected routes (architecture v1 Section 7.2). Other actors are enrolled by an
admin via `POST /v1/identity/enroll` (`phoenix identity enroll`) and sign the same
way with `--actor <name>`.

## Loading adapters

`POST /v1/adapters` (`phoenix lora load <spec>`) needs an actor with
`can_load_adapter` and a `"module.path:callable"` spec. The daemon imports the
module only if it is on the adapter allowlist:

- `phoenix.adapters.*`, Phoenix's own adapter package (for example
  `phoenix.adapters.identity_adapter:make_identity_adapter`). The package's own
  machinery (loader, registry, sandbox, validator, protocol, errors) is not
  loadable.
- Any namespace listed in `PHOENIX_ADAPTER_ALLOWLIST` in the **daemon's**
  environment: comma-separated dotted prefixes, e.g.
  `PHOENIX_ADAPTER_ALLOWLIST=acme_lora,my_org.adapters`. An entry admits that
  module and its submodules (`acme_lora.v6`), not look-alikes (`acme_lorax`).

Any other module (`os`, `subprocess`, an arbitrary installed package) gets
**HTTP 403** `adapter_module_not_allowed` and is never imported. The factory must
also be defined inside an allowlisted module. Before 2026-09-18 the daemon imported
whatever module a spec named; set the variable for any adapter package of your
own that lives outside `phoenix.adapters`.

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
rest_url: "http://127.0.0.1:8003"     # default; a loopback IP, not "localhost"
reproducibility_mode: "permissive"   # | "strict" | "replay"
default_actor: "adam"                 # actor the CLI signs as (loopback IP rest_url only);
                                      # omit it and the CLI sends unsigned requests
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
