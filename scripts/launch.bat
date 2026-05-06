@echo off
REM Phoenix v1 launcher -- Phase 0
REM Boots the empty Phoenix daemon (FastAPI on port 8003) and opens docs.
REM NATS JetStream and vendor-integrity check land in later phases.

setlocal

set PHOENIX_HOME=C:\Phoenix
set PHOENIX_PORT=8003

if not exist "%PHOENIX_HOME%\pyproject.toml" (
    echo ERROR: %PHOENIX_HOME% does not look like a Phoenix install.
    echo Expected pyproject.toml at %PHOENIX_HOME%\pyproject.toml
    exit /b 1
)

cd /d "%PHOENIX_HOME%"

echo Booting Phoenix daemon on port %PHOENIX_PORT%...
start /B python -m phoenix.api --port %PHOENIX_PORT%

REM Give the daemon a moment to start before opening the browser.
timeout /T 2 /NOBREAK > nul

echo Opening docs at http://localhost:%PHOENIX_PORT%/docs
start http://localhost:%PHOENIX_PORT%/docs

echo Phoenix v1 (Phase 0) running. Press Ctrl+C to stop.
pause
