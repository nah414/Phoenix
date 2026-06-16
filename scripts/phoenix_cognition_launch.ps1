# Phoenix Cognition launcher (Phase 13 Step 5c).
# Starts the Phoenix daemon if it isn't already running, then opens the
# cognition control panel in the default browser. Wired to the desktop
# shortcut created by scripts/install_desktop_shortcut.ps1.

$ErrorActionPreference = "SilentlyContinue"
$repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$port = 8003
$url = "http://127.0.0.1:$port/cognition"

$listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    # pythonw = no console window; fall back to python if absent.
    $py = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
    if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
    if ($py) {
        Start-Process -FilePath $py -ArgumentList '-m', 'phoenix.api' `
            -WorkingDirectory $repo -WindowStyle Hidden
        # Wait (up to ~20s) for the daemon to answer /health.
        for ($i = 0; $i -lt 40; $i++) {
            Start-Sleep -Milliseconds 500
            try {
                $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/health" `
                    -UseBasicParsing -TimeoutSec 2
                if ($r.StatusCode -eq 200) { break }
            } catch {}
        }
    }
}

Start-Process $url
