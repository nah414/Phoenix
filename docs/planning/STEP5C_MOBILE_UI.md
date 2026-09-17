# Step 5c — Mobile Control Panel (run it from your phone)

The Phoenix daemon serves a small web control panel (a PWA) for the cognition
harness at **`/cognition`**. It's the same capabilities as `phoenix cognition`
(audit · evaluate · adapt · train), drivable from a desktop browser or — over
Tailscale — your iPhone, no App Store / Xcode.

## Run it locally (desktop)

The easy way on Windows is the **desktop shortcut** (below): it starts the daemon
with a fresh random UI token and opens the panel already holding it. By hand:

```bash
# Any long random string; the panel needs the same value.
PHOENIX_UI_TOKEN=<a-long-random-string> python -m phoenix.api   # PowerShell: $env:PHOENIX_UI_TOKEN="..."
# open http://127.0.0.1:8003/cognition#token=<the same string>
```

The `#token=...` part is a URL *fragment*: browsers never send it to the server.
The panel reads it once, keeps it in `sessionStorage` for that tab, and strips it
from the address bar. (Or open `/cognition` and paste the token under
**Connection**.) Without `PHOENIX_UI_TOKEN` on the daemon every browser call to
`/v1/cognition/*` is a 401: there is no header-less or loopback-only mode.

Run **Audit** on `samples/step5c` data, **Train**/**Evaluate** on
`tests/cognition/fixtures/synthetic_corpus.jsonl` — the gate badge + per-class
metrics render inline. (`train` needs the `[ml-classifier]` extra.) If
`PHOENIX_CORPUS_DIR` is set (the shortcut sets it), copy those files into that
directory first; a relative path such as `felm_pairs.jsonl` is resolved inside it,
and paths outside it are refused with a 403 naming the directory (also shown under
**Connection**).

## Run it from your iPhone (Tailscale)

1. Install **Tailscale** on the desktop and the iPhone; sign both into the same
   tailnet (free for personal use).
2. Start the daemon bound beyond localhost, with a token (see security):
   ```bash
   set PHOENIX_UI_TOKEN=<a-long-random-string>     # PowerShell: $env:PHOENIX_UI_TOKEN="..."
   python -m phoenix.api --host 0.0.0.0 --port 8003
   ```
3. On the iPhone, open Safari to `http://<desktop-tailscale-name>:8003/cognition`
   (the MagicDNS name or Tailscale IP from the Tailscale app).
4. Open **Connection** in the panel, paste the token (a token typed there is
   remembered on that device), and tap **Share → Add to Home Screen**. You now have
   a full-screen app icon that drives the harness.

## Security model

- **Auth:** browsers can't do Phoenix's per-request HMAC, and the panel never gets
  an actor. (Until 2026-09-16 a header-less request was silently treated as the
  admin `adam` on *every* route; that is removed.) A `/v1/cognition/*` request is
  admitted only when one of these holds:
  1. **`PHOENIX_UI_TOKEN`** is set on the daemon and the request sends a matching
     `X-Phoenix-UI-Token` header (constant-time compare). When the token is set it
     is always required, on loopback too. The desktop shortcut generates one per
     launch and hands it over in the URL fragment (kept in `sessionStorage`, never
     `localStorage`); a token typed under **Connection** (the phone flow) is
     remembered in `localStorage` on that device.
  2. No token is set and the request carries a valid signed
     `Authorization: Phoenix-Actor ...` header (scripts; see
     `phoenix identity header`).

  Nothing else counts: not a loopback peer, `Host` or `Origin`. The short-lived
  `PHOENIX_UI_LOOPBACK_NO_TOKEN` opt-in (2026-09-16, pass 1) was replaced by the
  per-launch token and is ignored.
- **The UI token is not an admin credential.** It opens `/v1/cognition/*` only.
  Every other route (`/v1/admin/*`, `/v1/identity/*`, `/v1/tasks`, ...) requires a
  signed actor, so a tailnet peer with the token still cannot enroll actors, engage
  the kill switch, or load adapters.
- **Never expose the daemon on the public internet.** Tailscale keeps it on your
  private tailnet; `0.0.0.0` is safe *only* behind Tailscale (or a LAN you trust).
- **Path sandbox:** set `PHOENIX_CORPUS_DIR` to confine which files the endpoints
  may read/write. Relative paths are resolved inside it (not the daemon's working
  directory), and every path must still resolve inside it after `..` and symlinks,
  or the request is a 403 that names the directory. The desktop shortcut sets it to
  `%USERPROFILE%\.phoenix\corpora` unless you already set it. Unset → relative to
  the daemon's working directory, any path the process can access.
- **The health probe is unauthenticated.** `GET /v1/health` (used by the launcher
  and Docker/K8s probes) is open by design and returns the Phoenix version + vendor
  manifest; `PHOENIX_UI_TOKEN` gates only `/v1/cognition/*`, not this. On loopback
  that's immaterial; over Tailscale anyone on the tailnet can read those version
  strings (no corpus/secrets are exposed). If version disclosure matters, keep the
  daemon loopback-only or front it with a reverse proxy.

## Endpoints (for scripting / a future native client)

| Method · path | Body | Returns |
|---|---|---|
| `GET  /v1/cognition/corpora` | — | `{dir, files}` (`*.jsonl` under `PHOENIX_CORPUS_DIR`) |
| `POST /v1/cognition/audit` | `{corpus, min_per_class?}` | balance report + `ready` |
| `POST /v1/cognition/adapt` | `{dataset, path, out, ...}` | `{emitted, skipped, per_class}` |
| `POST /v1/cognition/evaluate` | `{corpus, model\|stub, confusion?}` | macro-F1 + gate + per-class |
| `POST /v1/cognition/train` | `{corpus, out, version?}` | `{job_id, status}` |
| `GET  /v1/cognition/jobs/{id}` | — | job status / result |

## Desktop shortcut (Windows)

A custom Phoenix icon and a one-click launcher ship with the repo:

```powershell
# create the "Phoenix Cognition" shortcut on your Desktop:
powershell -ExecutionPolicy Bypass -File scripts/install_desktop_shortcut.ps1
# (regenerate the icon if you tweak the design:)
python scripts/gen_app_icon.py
```

Double-clicking the shortcut runs `scripts/phoenix_cognition_launch.ps1`, which:

- if port 8003 is free, starts the daemon headlessly (via `pythonw`) on
  `127.0.0.1` with a fresh cryptographically random `PHOENIX_UI_TOKEN` (256 bits,
  generated per launch, passed only to that process) and, unless already set,
  `PHOENIX_CORPUS_DIR=%USERPROFILE%\.phoenix\corpora`. It waits for `/v1/health`
  (Phoenix's readiness probe — architecture v1 §5.2) and for the token to be
  accepted, then opens `http://127.0.0.1:8003/cognition#token=<token>` (~4s cold).
  The token rides in the URL fragment, never the query string, so it never reaches
  a request line or a server log;
- saves that token to `%USERPROFILE%\.phoenix\runtime\cognition_ui_token_8003`
  (user-private, next to the install master key), so a later double-click reopens
  the panel for the same running daemon instantly;
- if Phoenix is already serving but this launcher holds no token it accepts (a
  daemon started by hand, with no `PHOENIX_UI_TOKEN` or with a different one), it
  says exactly that in a dialog instead of opening a panel that would answer 401;
- if port 8003 is held by something that isn't Phoenix (a stuck/orphaned daemon
  or another app), it does **not** spawn a doomed second daemon — it reports the
  conflict and points you at Task Manager;
- never fails silently: any failure (Python missing, port conflict, slow start)
  pops a dismissible foreground dialog with the reason + a per-launch diagnostic
  log at `%TEMP%\phoenix-cognition-<pid>.log`.

Stop the daemon from Task Manager (the `pythonw` / `phoenix` process) when done.

The icon is `phoenix/ui/static/phoenix-cognition.ico` (multi-resolution). The same
mark ships as the PWA app icon (`apple-touch-icon.png` 180×180 + `icon-512.png`),
so the iOS "Add to Home Screen" icon is crisp too.

Pipeline reference: [`STEP5C_OPERATOR_GUIDE.md`](./STEP5C_OPERATOR_GUIDE.md).
