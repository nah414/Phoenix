# Step 5c — Mobile Control Panel (run it from your phone)

The Phoenix daemon serves a small web control panel (a PWA) for the cognition
harness at **`/cognition`**. It's the same capabilities as `phoenix cognition`
(audit · evaluate · adapt · train), drivable from a desktop browser or — over
Tailscale — your iPhone, no App Store / Xcode.

## Run it locally (desktop)

```bash
python -m phoenix.api            # serves on 127.0.0.1:8003
# open http://127.0.0.1:8003/cognition
```

Run **Audit** on `samples/step5c` data, **Train**/**Evaluate** on
`tests/cognition/fixtures/synthetic_corpus.jsonl` — the gate badge + per-class
metrics render inline. (`train` needs the `[ml-classifier]` extra.)

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
4. Open **Connection** in the panel, paste the token, and tap **Share → Add to
   Home Screen**. You now have a full-screen app icon that drives the harness.

## Security model

- **Auth:** browsers can't do Phoenix's per-request HMAC, so the panel uses the
  bootstrap actor (auto-granted on a personal install). When the daemon is bound
  beyond localhost, set **`PHOENIX_UI_TOKEN`** — every `/v1/cognition/*` request
  must then send a matching `X-Phoenix-UI-Token` header (the UI stores it in
  `localStorage`). Localhost-only use needs no token.
- **Never expose the daemon on the public internet.** Tailscale keeps it on your
  private tailnet; `0.0.0.0` is safe *only* behind Tailscale (or a LAN you trust).
- **Path sandbox (optional):** set `PHOENIX_CORPUS_DIR` to confine which files the
  endpoints may read/write (corpus paths must resolve inside it). Unset → any path
  the process can access (convenient for localhost).
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

- opens the panel immediately if Phoenix is already serving (this or another
  session — it verifies the **`/v1/health`** body, not merely any HTTP 200);
- if port 8003 is free, starts the daemon headlessly (via `pythonw`), waits for
  `/v1/health` (Phoenix's readiness probe — architecture v1 §5.2), then opens
  `http://127.0.0.1:8003/cognition` (~4s cold, instant when already up);
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
