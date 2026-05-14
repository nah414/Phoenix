# `vendor/actor/`

Vendored Actor authentication primitive consumed by Phoenix's
identity layer (Section 7.2). A minimal HMAC-signed dataclass
carrying `name`, `identity_fingerprint`, `issued_at`, and
`signature` -- enough to anchor the Section 7.3 actor-permissions
registry without dragging an OAuth-grade auth library into v1.

Per Section 11.7.1's vendor-verbatim discipline, this package
keeps its upstream formatting unchanged; Phoenix's identity layer
imports `actor.actor.Actor` via the sys.path injection wired in
`phoenix/__init__.py`.

Architectural reference: `PHOENIX_ARCHITECTURE_v1.md` Section 7.2
(Actor pattern), Section 11.7.1 (vendor discipline).
