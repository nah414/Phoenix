# Phoenix v1 distribution

Phoenix v1 ships **three** release artifacts per architecture v1 Section
1 Decision 29:

| Artifact | Audience | Install path | Size |
|---|---|---|---|
| pip wheel (`phoenix-middleware`) | Python developers integrating Phoenix into an existing service | `pip install phoenix-middleware` | ~500 KB |
| Docker image (`ghcr.io/nah414/phoenix:<tag>`) | Ops teams running Phoenix as a container | `docker run ...` | ~250 MB |
| Nuitka standalone binary | Non-developer users who want a single-executable install | Download from GitHub Releases | ~80 MB |

Each artifact boots the same Phoenix daemon and (where applicable) the
same bundled NATS JetStream queue per Section 1 Decision 33. The
launcher's `--external-daemon` / `--external-nats` flags are the
opt-out for installs that run the components separately.

## Choosing an artifact

- **Pip wheel** is the right choice if you're embedding Phoenix into a
  larger Python codebase, want a `pip-tools`-managed dependency, or
  need to vendor-patch Phoenix for an internal fork.
- **Docker image** is the right choice if you want isolation,
  reproducible boots, easy roll-back, and the bundled NATS without
  installing it yourself.
- **Standalone binary** is the right choice if you want a no-Python,
  no-Docker, double-click install that "just works" -- typically the
  laptop / single-user-server case.

## Continuing on

- [`install.md`](install.md) -- step-by-step install instructions
  for each artifact.
- [`run.md`](run.md) -- runtime topology, the two-process model,
  `--external-daemon` semantics, healthcheck endpoints.

For the threat model behind the install ergonomics, see
`PHOENIX_ARCHITECTURE_v1.md` Section 7.2 (same-OS-user vector accepted;
no privilege elevation at install time).
