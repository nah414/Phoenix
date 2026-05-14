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
curl http://localhost:8003/v1/health
```

Notes:
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

## Verifying an install

Regardless of which artifact you used:

```bash
# 1. Phoenix self-test:
phoenix --version             # prints the version string
phoenix health                # 200 OK from the daemon (after boot)

# 2. End-to-end task probe:
phoenix task submit --spec '@examples/qho_task.json'
```

If `phoenix health` returns a non-200 status or times out, see
[`run.md`](run.md) for the boot diagnostic flow.
