# Phoenix v1 install

Step-by-step instructions for each of the three v1 release artifacts.
For run-time topology + flag semantics, see [`run.md`](run.md).

## Pip wheel

Python 3.11, 3.12, or 3.13 supported. Linux and Windows are CI-tested;
macOS support deferred to v1.1 (build from source via the sdist).

```bash
# Minimal install (SQLite state, no NATS, no MCP):
pip install phoenix-middleware

# With Postgres state backend:
pip install 'phoenix-middleware[postgres]'

# With NATS JetStream queue (requires nats-server on PATH):
pip install 'phoenix-middleware[nats]'

# With OpenTelemetry export adapter:
pip install 'phoenix-middleware[otel]'

# With MCP server (Claude Code / Cursor / Cline / etc.):
pip install 'phoenix-middleware[mcp]'

# Everything:
pip install 'phoenix-middleware[postgres,nats,otel,mcp]'
```

After install, two console entry points are on `PATH`:
- `phoenix` -- the CLI surface (`phoenix --help`, `phoenix health`,
  `phoenix task submit ...`).
- `python -m phoenix` -- the launcher (boots the daemon + NATS).

The daemon-only entry stays at `python -m phoenix.api` for scripts
that want to manage the daemon's lifecycle directly.

**One-time CLI setup.** Authenticated routes (everything but `/v1/health` and the
docs pages; see [run.md](run.md#authentication) for the UI-token and WebSocket
exceptions) need a signed actor, and the CLI never signs as anyone implicitly.
After the daemon has started once (it creates the install key), name the actor as
the OS user that owns the install, next to the daemon's address:

```yaml
# ~/.phoenix/config.yaml
rest_url: "http://127.0.0.1:8003"   # the daemon's default address (and the CLI default)
default_actor: "adam"
```

Use the `127.0.0.1` form, not `localhost`: `default_actor` is signed only for a
loopback IP address (see [run.md](run.md#authentication) for why, and for
`--actor`, remote daemons and `phoenix identity header`). If you start the daemon
on another port, change `rest_url` to match.

## Docker image

The image is published to GitHub Container Registry at
`ghcr.io/nah414/phoenix:<version>` (and `:latest`) on every release tag.

```bash
# Pull and run:
docker pull ghcr.io/nah414/phoenix:1.0.0rc1
docker run -d --rm \
    -p 8003:8003 -p 4222:4222 \
    -v phoenix-state:/home/phoenix/.phoenix \
    --name phoenix \
    ghcr.io/nah414/phoenix:1.0.0rc1

# Verify:
curl http://127.0.0.1:8003/v1/health

# Authenticated calls need a signed actor. The install key lives in the container,
# so sign inside it and name the actor (the CLI never signs implicitly). The image
# sets PHOENIX_REST_URL=http://127.0.0.1:8003, so the in-container CLI reaches its
# daemon:
docker exec phoenix phoenix --actor adam audit verify
curl -H "Authorization: $(docker exec phoenix phoenix --actor adam identity header)" \
    http://127.0.0.1:8003/v1/admin/health/detailed
```

Notes:
- Publishing port 8003 exposes the API, but every HTTP route except `/v1/health`
  (and the docs pages) requires an HMAC-signed `Authorization: Phoenix-Actor ...`
  header (HTTP 401 otherwise). Exceptions: `/v1/cognition/*` also accepts
  `X-Phoenix-UI-Token` when the container is started with `PHOENIX_UI_TOKEN` set,
  and the WebSockets under `/v1/ws/*` take a single-use `token` query parameter
  (60 s) minted by `POST /v1/identity/ws-token`, which needs the signed header.
  Header-less requests are never treated as an admin. See
  [run.md](run.md#authentication).
- A CLI on the host cannot sign for the container: the install key is in the
  `phoenix-state` volume, and the host's own `~/.phoenix/runtime/master_key.bin`
  (if any) is a different key. Do not set `default_actor` on the host for a
  containerized daemon; run authenticated commands with
  `docker exec phoenix phoenix --actor adam ...` instead. `phoenix health` works
  from the host: `/v1/health` is never signed, and the CLI's default `rest_url`
  (`http://127.0.0.1:8003`) is the published port (set `rest_url` if you publish
  another one).
- Rebuild any image built before the 2026-09-16 authentication change: it lacks
  the fix, and its in-container CLI defaults to port 8000 (until you rebuild, pass
  `--rest-url http://127.0.0.1:8003` after the second `phoenix`).
- The image runs as **non-root UID 1000** (user `phoenix`).
- Both Phoenix (8003) and NATS (4222) ports are exposed; the
  monitoring port (8222) is internal.
- State persistence: mount `/home/phoenix/.phoenix` to a Docker volume
  or bind-mount path. The container stores:
  - `state/` -- SQLite state backend
  - `identity/` -- Ed25519 keystore
  - `audit/` -- audit-log JSONL files
  - `runtime/nats/` -- JetStream file storage

To build from source (advanced; the published image is usually
preferable):

```bash
git clone https://github.com/nah414/Phoenix
cd Phoenix
docker build -t phoenix:local .
```

## Standalone binary

Download the appropriate binary from the
[GitHub Releases page](https://github.com/nah414/Phoenix/releases):

- `phoenix-windows-x64.exe` -- Windows 10/11, x86-64.
- `phoenix-linux-x64` -- glibc-2.31+ Linux (Ubuntu 20.04+, Debian 11+,
  RHEL 9+, recent Fedora/Arch).

```bash
# Linux:
curl -L -o phoenix https://github.com/nah414/Phoenix/releases/download/v1.0.0rc1/phoenix-linux-x64
chmod +x phoenix
./phoenix --version
./phoenix                    # boots daemon + (if installed) NATS, opens docs URL

# Windows (PowerShell):
Invoke-WebRequest -Uri https://github.com/nah414/Phoenix/releases/download/v1.0.0rc1/phoenix-windows-x64.exe -OutFile phoenix.exe
.\phoenix.exe --version
.\phoenix.exe                # boots daemon + (if installed) NATS, opens docs URL
```

**SmartScreen on Windows:** the v1.0.rc binary is **unsigned**; the
first launch on Windows triggers a SmartScreen "Unrecognized app"
warning. Click "More info" then "Run anyway". Code signing lands in
the v1.0 final release.

**glibc floor on Linux:** the binary is compiled on Ubuntu 20.04
(glibc 2.31), which covers ~99% of currently-supported Linux distros.
Very old distros (CentOS 7, Ubuntu 18.04) need to use the pip wheel
or Docker image instead.

**NATS on standalone:** the binary does NOT bundle nats-server. If you
want the full two-process model, install nats-server separately:
- **Windows:** `winget install NATSAuthors.NATSServer`
- **Linux:** download the static binary from
  https://github.com/nats-io/nats-server/releases

Without NATS, Phoenix runs in single-process mode (no JetStream queue;
the SQLite state backend handles task durability). Phoenix's CLI
prints a clear "NATS not found, continuing without it" message at
boot when this happens.

**One-time CLI setup:** same as the pip wheel. After the first boot,
add `rest_url: "http://127.0.0.1:8003"` and `default_actor: "adam"` to
`~/.phoenix/config.yaml` (Windows: `%USERPROFILE%\.phoenix\config.yaml`); see
[run.md](run.md#authentication).

## Verifying an install

Pip wheel or standalone binary:

```bash
# 1. Phoenix self-test (no actor needed; /v1/health is never signed):
phoenix --version             # prints the version string
phoenix health                # 200 OK from the daemon at rest_url (default http://127.0.0.1:8003)

# 2. One-time setup, if not done yet (see run.md#authentication):
#    add  rest_url: "http://127.0.0.1:8003"  and  default_actor: "adam"
#    to ~/.phoenix/config.yaml

# 3. End-to-end task probe (authenticated; 401 without step 2):
phoenix task submit --spec '@examples/qho_task.json'
```

Docker image (the host CLI cannot sign for the container, so sign inside it):

```bash
curl http://127.0.0.1:8003/v1/health   # or `phoenix health` from a host CLI (never signed)
docker exec phoenix phoenix --actor adam task submit --spec '<inline JSON or @path inside the container>'
```

If `phoenix health` returns a non-200 status or times out, see
[`run.md`](run.md) for the boot diagnostic flow.
