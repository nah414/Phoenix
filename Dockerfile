# Phoenix v1 Docker image -- Phase 12 Step 4.
#
# Multi-stage build:
#   Stage 1 (builder) -- python:3.12-slim + build deps + curl; fetches
#     nats-server release tarball, verifies checksum, and builds the
#     Phoenix wheel from the working tree.
#   Stage 2 (runtime) -- python:3.12-slim minus build deps; installs
#     the wheel + nats extra, copies the nats-server binary, runs as
#     a non-root UID-1000 user.
#
# Per architecture v1 Section 1 Decision 33, a Phoenix container boots
# both Phoenix and NATS under the bundled launcher (`python -m phoenix`).
# Per Section 11.3.3 RESOLVED, the launcher's --external-daemon flag
# is the opt-out for installs that run Phoenix's daemon separately.
#
# Per Section 10.4, vendor/ is frozen at distribution-build time: the
# wheel that lands in this image was built once from the sdist tree and
# never re-syncs at runtime. vendor_sync.py does not run inside the
# container.
#
# Build:
#   docker build -t phoenix:1.0.0rc1 .
#
# Run (bundled daemon + NATS):
#   docker run -d --rm -p 8003:8003 -p 4222:4222 phoenix:1.0.0rc1
#
# Run (external NATS, daemon only):
#   docker run -d --rm -p 8003:8003 \
#       -e PHOENIX_NATS_URL=nats://your-nats:4222 \
#       phoenix:1.0.0rc1 --external-nats

ARG PYTHON_VERSION=3.12
ARG NATS_VERSION=2.10.22

# ---------------------------------------------------------------------
# Stage 1: builder -- fetch nats, build wheel.
# ---------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

ARG NATS_VERSION

# Build tools needed for any source-distribution deps that don't have
# precompiled wheels for slim's manylinux platform. numpy/scipy ship
# precompiled wheels for cp311+ on x86_64; pyyaml has wheels. The
# build-essential layer stays small.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Fetch nats-server binary from the official GitHub release.
# Pin to a specific NATS version; review during vendor sync cadence.
# The checksum is verified against the SHA256SUMS file published with
# each release; if the upstream URL drifts, the build fails loudly.
WORKDIR /tmp/nats-fetch
RUN curl -fsSL "https://github.com/nats-io/nats-server/releases/download/v${NATS_VERSION}/nats-server-v${NATS_VERSION}-linux-amd64.tar.gz" \
        -o nats-server.tar.gz \
    && curl -fsSL "https://github.com/nats-io/nats-server/releases/download/v${NATS_VERSION}/SHA256SUMS" \
        -o SHA256SUMS \
    && grep "nats-server-v${NATS_VERSION}-linux-amd64.tar.gz" SHA256SUMS | sha256sum -c - \
    && tar -xzf nats-server.tar.gz \
    && mv "nats-server-v${NATS_VERSION}-linux-amd64/nats-server" /usr/local/bin/nats-server \
    && chmod +x /usr/local/bin/nats-server

# Build the Phoenix wheel from the working tree.
WORKDIR /build
COPY . .
RUN pip install --no-cache-dir build \
    && python -m build --wheel --outdir /wheels

# ---------------------------------------------------------------------
# Stage 2: runtime -- slim image with just the wheel + nats-server.
# ---------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

# Copy nats-server binary from builder stage.
COPY --from=builder /usr/local/bin/nats-server /usr/local/bin/nats-server

# Install the Phoenix wheel + nats extra (the runtime needs nats-py).
# --no-deps + the wheel's own dependency closure are pulled from PyPI
# in one shot via the [nats] extra.
COPY --from=builder /wheels/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl[nats] \
    && rm /tmp/*.whl \
    && pip cache purge

# Non-root user -- UID 1000, no shell needed for Phoenix's process tree.
# /home/phoenix is the JetStream store + identity keystore home.
RUN useradd -r -u 1000 -m -d /home/phoenix -s /usr/sbin/nologin phoenix \
    && mkdir -p /home/phoenix/.phoenix/runtime/nats \
                /home/phoenix/.phoenix/state \
                /home/phoenix/.phoenix/identity \
                /home/phoenix/.phoenix/audit \
    && chown -R phoenix:phoenix /home/phoenix

USER phoenix
WORKDIR /home/phoenix

# Daemon binds to 0.0.0.0 inside the container so the host (or another
# container) can reach it via the published port. The container itself
# is the security boundary; loopback-only inside the container would
# defeat the purpose of running in Docker.
ENV PHOENIX_HOST=0.0.0.0 \
    PHOENIX_PORT=8003 \
    PHOENIX_NATS_URL=nats://127.0.0.1:4222 \
    PYTHONUNBUFFERED=1

EXPOSE 8003 4222

# Healthcheck uses httpx (already in main deps) -- no extra packages.
# --start-period gives the daemon + NATS time to bind on cold start.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import httpx, sys; r = httpx.get('http://localhost:${PHOENIX_PORT}/v1/health', timeout=3.0); sys.exit(0 if r.status_code == 200 else 1)"

ENTRYPOINT ["python", "-m", "phoenix"]
CMD ["--host", "0.0.0.0", "--no-browser"]
