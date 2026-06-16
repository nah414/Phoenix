# Phoenix Cognition launcher (Phase 13 Step 5c).
# Starts the Phoenix daemon if it isn't already running, waits for it to become
# ready, then opens the cognition control panel in the default browser. Wired to
# the desktop shortcut created by scripts/install_desktop_shortcut.ps1.
#
# Pass -NoBrowser to start/await the daemon without opening a browser (used by
# tests and headless checks).

param([switch]$NoBrowser)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$port = 8003
$url = "http://127.0.0.1:$port/cognition"
# Phoenix's readiness probe is /v1/health (architecture v1 Section 5.2), NOT
# /health. Polling the wrong path is what made the shortcut appear to hang.
$health = "http://127.0.0.1:$port/v1/health"
$log = Join-Path $env:TEMP "phoenix-cognition.log"

function Show-Problem([string]$message) {
    # The shortcut runs hidden, so a failure must never be a silent no-op:
    # pop a dismissible dialog (auto-closes after 60s) telling the user why.
    try {
        $wsh = New-Object -ComObject WScript.Shell
        $wsh.Popup($message, 60, "Phoenix Cognition", 0x10) | Out-Null
    } catch {}
}

function Test-Ready {
    try {
        $r = Invoke-WebRequest -Uri $health -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

# Already running (this or another session)? Just open the panel.
if (Test-Ready) {
    if (-not $NoBrowser) { Start-Process $url }
    exit 0
}

# pythonw = no console window; fall back to python if it isn't present.
$py = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $py) {
    Show-Problem "Python was not found on PATH.`n`nInstall Python (or Anaconda) and make sure 'python' is on PATH, then use the shortcut again."
    exit 1
}

# Start the daemon detached, capturing its output for diagnosis on failure.
try {
    Start-Process -FilePath $py -ArgumentList '-m', 'phoenix.api' `
        -WorkingDirectory $repo -WindowStyle Hidden `
        -RedirectStandardError $log -RedirectStandardOutput "$log.out"
} catch {
    Show-Problem "Could not start the Phoenix daemon:`n`n$($_.Exception.Message)"
    exit 1
}

# Wait up to ~40s for the daemon to answer /v1/health (cold first-run imports
# can be slow). Break as soon as it's ready.
$ready = $false
for ($i = 0; $i -lt 80; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-Ready) { $ready = $true; break }
}

if (-not $ready) {
    Show-Problem "The Phoenix daemon did not become ready on port $port within 40 seconds.`n`nDiagnostic log:`n$log"
    exit 1
}

if (-not $NoBrowser) { Start-Process $url }
exit 0
