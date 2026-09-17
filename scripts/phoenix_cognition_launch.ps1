# Phoenix Cognition launcher (Phase 13 Step 5c).
# Starts the Phoenix daemon if it isn't already running, waits for it to become
# ready, then opens the cognition control panel in the default browser. Wired to
# the desktop shortcut created by scripts/install_desktop_shortcut.ps1.
#
# Auth (2026-09-16): the cognition UI endpoints require PHOENIX_UI_TOKEN (or a
# signed Phoenix-Actor header); nothing is admitted for being on loopback. For a
# daemon it starts, this launcher generates a cryptographically random token per
# launch, passes it to that daemon only (environment of the child process), and
# opens the panel as /cognition#token=<token>. The token rides in the URL
# FRAGMENT, never the query string: browsers do not send a fragment to the
# server, so it stays out of request lines and logs. app.js moves it into
# sessionStorage and strips it from the address bar. The token is also saved to
# %USERPROFILE%\.phoenix\runtime\cognition_ui_token_<port> (user-private, next to
# the install master key) so a later double-click can reopen the panel for the
# same running daemon. A running daemon this launcher has no valid token for is
# reported clearly instead of opening a panel that answers 401.
#
# Switches / parameters (the shortcut passes none):
#   -NoBrowser  start/await the daemon without opening a browser.
#   -Quiet      send failure messages to stderr instead of a popup (for tests).
#   -Port <n>   daemon port (default 8003).

param([switch]$NoBrowser, [switch]$Quiet, [int]$Port = 8003)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
# ($port below is the -Port parameter: PowerShell variable names are case-insensitive.)
$url = "http://127.0.0.1:$port/cognition"
# Phoenix's readiness probe is /v1/health (architecture v1 Section 5.2), NOT
# /health. Polling the wrong path is what made the shortcut appear to hang.
$health = "http://127.0.0.1:$port/v1/health"
# Authenticated cognition-UI probe: 200 only with the daemon's PHOENIX_UI_TOKEN.
$uiProbe = "http://127.0.0.1:$port/v1/cognition/corpora"
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
# How long to wait for a sibling launch (that just won the port) to save its token.
$tokenWaitSec = 5
# Phoenix's per-user data dir (Python's Path.home() is %USERPROFILE% on Windows).
$userHome = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
$phoenixHome = Join-Path $userHome ".phoenix"
$tokenFile = Join-Path $phoenixHome "runtime\cognition_ui_token_$port"

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

function New-UiToken {
    # 256 bits from the OS CSPRNG, base64url-encoded (safe in a URL fragment and
    # an HTTP header).
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Read-SavedToken {
    if (-not (Test-Path -LiteralPath $tokenFile)) { return $null }
    try {
        $saved = [System.IO.File]::ReadAllText($tokenFile).Trim()
    } catch {
        return $null
    }
    if ($saved) { return $saved }
    return $null
}

function Save-UiToken([string]$token) {
    try {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $tokenFile) | Out-Null
        [System.IO.File]::WriteAllText($tokenFile, $token)
    } catch {
        # Not fatal: this launch still opens the panel; a later double-click
        # just cannot reuse the running daemon.
    }
}

function Invoke-UiProbe([string]$token) {
    # GET /v1/cognition/corpora, optionally with a UI token. Returns the status
    # (0 on a transport failure) and the response body.
    $headers = @{}
    if ($token) { $headers["X-Phoenix-UI-Token"] = $token }
    try {
        $r = Invoke-WebRequest -Uri $uiProbe -Headers $headers -UseBasicParsing -TimeoutSec 5
        return @{ Status = [int]$r.StatusCode; Body = [string]$r.Content }
    } catch {
        $status = 0
        $body = ""
        $resp = $_.Exception.Response
        if ($null -ne $resp) {
            try { $status = [int]$resp.StatusCode } catch {}
        }
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            $body = [string]$_.ErrorDetails.Message
        } elseif ($null -ne $resp) {
            try {
                $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
                $body = $reader.ReadToEnd()
            } catch {}
        }
        return @{ Status = $status; Body = $body }
    }
}

function Open-Panel([string]$token) {
    if ($NoBrowser) { return }
    # Token in the fragment, never the query string (see the header comment).
    $panelUrl = "$url#token=$token"
    try {
        Start-Process $panelUrl
    } catch {
        Show-Problem "Phoenix is running, but Windows could not open a browser ($($_.Exception.Message)).`n`nOpen this address manually. It carries this launch's cognition UI token, so do not share it:`n$panelUrl"
    }
}

function Open-RunningDaemon {
    # Phoenix is already serving on $port (another session's launch, or started by
    # hand). Open the panel only with a token that daemon accepts: a panel that
    # answers every request with 401 is worse than a clear message.
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        $saved = Read-SavedToken
        if ($saved -and (Invoke-UiProbe $saved).Status -eq 200) {
            Open-Panel $saved
            exit 0
        }
        $anon = Invoke-UiProbe ""
        $hasToken = ($anon.Status -eq 401 -and $anon.Body -like "*missing or invalid X-Phoenix-UI-Token*")
        # A sibling launch that just won the port saves its token right after its
        # daemon answers, so give it a moment. A daemon with no token was not
        # started by this launcher, so there is nothing to wait for.
        if (-not $hasToken -or $sw.Elapsed.TotalSeconds -ge $tokenWaitSec) { break }
        Start-Sleep -Milliseconds 500
    }
    $stopHint = "Stop it (Task Manager: end the pythonw / python process running phoenix.api), then use the shortcut again: it starts the daemon with a fresh per-launch token."
    if ($hasToken) {
        Show-Problem "Phoenix is already running on port $port with a cognition UI token that this launcher does not have (it was started by hand with PHOENIX_UI_TOKEN, or its saved token is gone).`n`nThe control panel would answer every request with 401 without that token. Open $url and paste that daemon's PHOENIX_UI_TOKEN under Connection, or:`n`n$stopHint"
    } elseif ($anon.Status -eq 401) {
        Show-Problem "Phoenix is already running on port $port, but WITHOUT a cognition UI token (PHOENIX_UI_TOKEN is not set for that daemon), so the control panel would answer every request with 401.`n`nIt was started outside this shortcut (for example 'python -m phoenix.api').`n`n$stopHint"
    } elseif ($anon.Status -eq 200) {
        Show-Problem "Phoenix is already running on port $port, but it is an older build that serves the cognition UI without any token.`n`n$stopHint"
    } else {
        Show-Problem "Phoenix is running on port $port, but its cognition UI did not answer as expected (HTTP $($anon.Status)).`n`n$stopHint"
    }
    exit 1
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

# 1. Already serving (this or another session)? Open the panel if we hold a
#    token that daemon accepts; otherwise explain why not.
if (Test-Ready) { Open-RunningDaemon }

# 2. Port already occupied but not (yet) answering as Phoenix. Could be a
#    sibling daemon still starting, or a foreign/stuck process. Poll briefly;
#    open if Phoenix comes up, otherwise report a port conflict. We do NOT try
#    to start our own daemon onto an occupied port (it would just fail to bind).
if (Test-PortOpen) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $occupiedGraceSec) {
        Start-Sleep -Milliseconds 500
        if (Test-Ready) { Open-RunningDaemon }
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

# Per-launch cognition UI token for the daemon started below. Environment
# variables set here are inherited only by that child process. The daemon binds
# 127.0.0.1 (the default); the token opens /v1/cognition/* only, never an actor
# for any other route (admin endpoints still need a signed Phoenix-Actor header).
$token = New-UiToken
$env:PHOENIX_UI_TOKEN = $token
# Confine the UI's file access (read corpora, write adapted corpora and models)
# to one directory unless the operator already chose one.
if (-not $env:PHOENIX_CORPUS_DIR) {
    $env:PHOENIX_CORPUS_DIR = Join-Path $phoenixHome "corpora"
}
try { New-Item -ItemType Directory -Force -Path $env:PHOENIX_CORPUS_DIR | Out-Null } catch {}

try {
    $proc = Start-Process -FilePath $py -ArgumentList '-m', 'phoenix.api', '--port', "$port" `
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
    if (Test-Ready) {
        $probeStatus = (Invoke-UiProbe $token).Status
        if ($probeStatus -eq 200) { $ready = $true; break }
        if ($probeStatus -eq 401) {
            # Phoenix answers, but not with this launch's token: a sibling launch
            # won the port. Ours cannot bind; stop it and use the sibling's.
            if (-not $proc.HasExited) {
                try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
            }
            Open-RunningDaemon
        }
    }
    if ($proc.HasExited) {
        # Our daemon stopped. If a sibling won the port and is now serving,
        # use it; otherwise surface the failure with the captured log.
        if (Test-Ready) { Open-RunningDaemon }
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

Save-UiToken $token
Open-Panel $token
exit 0
