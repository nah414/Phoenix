@echo off
REM Phoenix v1 launcher with embedded NATS -- Phase 6b Step 5 demo.
REM
REM Per architecture Decision 33: a solo Phoenix install boots two
REM processes -- Phoenix itself + NATS -- managed by the same launcher.
REM This script demonstrates the two-process model: starts NATS with
REM JetStream file-backed storage, then starts the Phoenix daemon.
REM
REM Prerequisites:
REM   - nats-server installed (winget install nats-io.nats-server)
REM   - Phoenix-middleware[nats] installed (pip install -e .[nats])
REM
REM Per locked open-item 3 (2026-05-10): the nats-server binary is
REM user-installed for 1.0.0.dev7. Bundling lands in the 1.0.0
REM release-artifact pipeline (Phase 10).

setlocal

set PHOENIX_HOME=C:\Phoenix
set PHOENIX_PORT=8003
set NATS_PORT=4222
set NATS_MONITOR_PORT=8222
set NATS_STORE_DIR=%USERPROFILE%\.phoenix\runtime\nats

if not exist "%PHOENIX_HOME%\pyproject.toml" (
    echo ERROR: %PHOENIX_HOME% does not look like a Phoenix install.
    echo Expected pyproject.toml at %PHOENIX_HOME%\pyproject.toml
    exit /b 1
)

where nats-server >nul 2>&1
if errorlevel 1 (
    echo ERROR: nats-server not found on PATH.
    echo Install via: winget install nats-io.nats-server
    echo Or set NATS_SERVER_PATH to the binary location.
    exit /b 1
)

if not exist "%NATS_STORE_DIR%" mkdir "%NATS_STORE_DIR%"

cd /d "%PHOENIX_HOME%"

echo Booting NATS (port %NATS_PORT%, monitor %NATS_MONITOR_PORT%, store %NATS_STORE_DIR%)...
start "Phoenix NATS" /B nats-server --jetstream --store_dir "%NATS_STORE_DIR%" --port %NATS_PORT% --http_port %NATS_MONITOR_PORT%

REM Wait briefly for NATS to come up. Production should check the
REM monitoring port; this demo just sleeps.
timeout /T 3 /NOBREAK > nul

echo Booting Phoenix daemon on port %PHOENIX_PORT%...
set PHOENIX_NATS_URL=nats://127.0.0.1:%NATS_PORT%
python -m phoenix.api --port %PHOENIX_PORT%

REM Phoenix exited -- stop NATS.
echo Stopping NATS...
taskkill /IM nats-server.exe /F > nul 2>&1

endlocal
