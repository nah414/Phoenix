# Phoenix Cognition launcher (Phase 13 Step 5c).
# Starts the Phoenix daemon if it isn't already running, waits for it to become
# ready, then opens the cognition control panel in the default browser. Wired to
# the desktop shortcut created by scripts/install_desktop_shortcut.ps1.
#
# Switches (the shortcut passes none):
#   -NoBrowser  start/await the daemon without opening a browser.
#   -Quiet      send failure messages to stderr instead of a popup (for tests).

param([switch]$NoBrowser, [switch]$Quiet)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$port = 8003
$url = "http://127.0.0.1:$port/cognition"
# Phoenix's readiness probe is /v1/health (architecture v1 Section 5.2), NOT
# /health. Polling the wrong path is what made the shortcut appear to hang.
$health = "http://127.0.0.1:$port/v1/health"
# Per-launch log files so concurrent launches never truncate each other's
# diagnostics (the user runs several sessions at once).
$log = Join-Path $env:TEMP "phoenix-cognition-$PID.log"
$out = Join-Path $env:TEMP "phoenix-cognition-$PID.out"
# Cold first-run imports + antivirus scanning can be slow.
$readyTimeoutSec = 90
# When the port is already occupied but not yet answering as Phoenix, this is
# how long we let a possibly-still-starting daemon (another session) come up
# before declaring a port conflict. uvicorn does NOT exit promptly on a bind
# clash, so we never spawn a daemon onto an occupied port.
$occupiedGraceSec = 20

function Show-Problem([string]$message) {
    # The shortcut runs hidden, so a failure must never be a silent no-op.
    if ($Quiet) {
        [Console]::Error.WriteLine("phoenix-cognition: $message")
        return
    }
    # Icon (0x10) + SetForeground (0x10000) + TopMost (0x40000) so the dialog
    # surfaces in front of the active window; auto-dismisses after 120s.
    try {
        $wsh = New-Object -ComObject WScript.Shell
        $wsh.Popup($message, 120, "Phoenix Cognition", (0x10 -bor 0x10000 -bor 0x40000)) | Out-Null
    } catch {}
}

function Open-Panel {
    if ($NoBrowser) { return }
    try {
        Start-Process $url
    } catch {
        Show-Problem "Phoenix is running at:`n$url`n`n...but Windows could not open a browser ($($_.Exception.Message)). Open that URL manually."
    }
}

function Test-Ready {
    # Ready only when *Phoenix* answers 200 on /v1/health -- a foreign service
    # squatting port $port must not be mistaken for the daemon.
    try {
        $r = Invoke-WebRequest -Uri $health -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -ne 200) { return $false }
        $j = $r.Content | ConvertFrom-Json
        return ($j.status -eq 'ok' -and $null -ne $j.phoenix_version)
    } catch {
        return $false
    }
}

function Test-PortOpen {
    # Fast TCP probe (loopback refuses a closed port almost instantly, so the
    # cold-start path pays only a few ms). Avoids Test-NetConnection, which
    # stalls for seconds on a closed port.
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $port, $null, $null)
        if ($async.AsyncWaitHandle.WaitOne(500)) {
            $client.EndConnect($async)
            return $true
        }
        return $false
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Resolve-Python {
    # pythonw = no console window; fall back to python. Reject the Microsoft
    # Store "app execution alias" stub under WindowsApps -- it doesn't run
    # Python, it opens the Store.
    foreach ($name in 'pythonw', 'python') {
        foreach ($cmd in (Get-Command $name -All -ErrorAction SilentlyContinue)) {
            if ($cmd.Source -and $cmd.Source -notlike "*\WindowsApps\*") { return $cmd.Source }
        }
    }
    return $null
}

# 1. Already serving (this or another session)? Just open the panel.
if (Test-Ready) { Open-Panel; exit 0 }

# 2. Port already occupied but not (yet) answering as Phoenix. Could be a
#    sibling daemon still starting, or a foreign/stuck process. Poll briefly;
#    open if Phoenix comes up, otherwise report a port conflict. We do NOT try
#    to start our own daemon onto an occupied port (it would just fail to bind).
if (Test-PortOpen) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $occupiedGraceSec) {
        Start-Sleep -Milliseconds 500
        if (Test-Ready) { Open-Panel; exit 0 }
    }
    Show-Problem "Port $port is in use but is not answering Phoenix's health check.`n`nThis is usually a stuck or orphaned Phoenix daemon, or another app on port $port. End the pythonw / phoenix process in Task Manager (or free the port), then try the shortcut again."
    exit 1
}

# 3. Port is free -> start the daemon ourselves.
$py = Resolve-Python
if (-not $py) {
    Show-Problem "Python was not found on PATH (the Microsoft Store stub does not count).`n`nInstall Python (or Anaconda) and make sure 'python' is on PATH, then use the shortcut again."
    exit 1
}

try {
    $proc = Start-Process -FilePath $py -ArgumentList '-m', 'phoenix.api' `
        -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
        -RedirectStandardError $log -RedirectStandardOutput $out
} catch {
    Show-Problem "Could not start the Phoenix daemon:`n`n$($_.Exception.Message)"
    exit 1
}

# Wait for readiness; short-circuit if the daemon dies (in a rare cold
# double-launch another session may have won the port first).
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$ready = $false
while ($sw.Elapsed.TotalSeconds -lt $readyTimeoutSec) {
    Start-Sleep -Milliseconds 500
    if (Test-Ready) { $ready = $true; break }
    if ($proc.HasExited) {
        # Our daemon stopped. If a sibling won the port and is now serving,
        # use it; otherwise surface the failure with the captured log.
        if (Test-Ready) { Open-Panel; exit 0 }
        $tail = ""
        try { $tail = Get-Content $log -Raw -ErrorAction SilentlyContinue } catch {}
        Show-Problem ("The Phoenix daemon stopped right after starting (exit $($proc.ExitCode)).`n`n" +
            "Log: $log`n`n$tail")
        exit 1
    }
}

if (-not $ready) {
    # Still not answering and still alive: reap the stuck process so it does not
    # keep holding the port, and point the user at the log.
    $pidNote = ""
    if (-not $proc.HasExited) {
        try {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            $pidNote = " (stopped PID $($proc.Id))"
        } catch {}
    }
    Show-Problem "Phoenix took longer than $readyTimeoutSec seconds to start$pidNote.`n`nThe first run after a reboot can be slow (antivirus scans the imports). Try the shortcut again, or check the log:`n$log"
    exit 1
}

Open-Panel
exit 0
