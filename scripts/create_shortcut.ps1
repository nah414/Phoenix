# Phoenix v1 -- desktop shortcut installer
# Creates a Windows .lnk on the user's Desktop pointing at scripts/launch.bat.
# Per architecture v1 Section 10.5: every Phoenix release ships its own desktop
# launcher, never modifies dr-frank-and-eddy's launcher.

$ErrorActionPreference = "Stop"

$PhoenixHome = "C:\Phoenix"
$LauncherBat = Join-Path $PhoenixHome "scripts\launch.bat"
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopPath "Phoenix.lnk"

if (-not (Test-Path $LauncherBat)) {
    Write-Error "Launcher not found at $LauncherBat. Run from a checked-out Phoenix install."
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $LauncherBat
$shortcut.WorkingDirectory = $PhoenixHome
# Phase 0 placeholder icon (uses launcher .bat icon). Designed icon lands
# before public release per architecture Section 11.7.2.
$shortcut.IconLocation = "$LauncherBat,0"
$shortcut.Description = "Phoenix v1 -- quantum-accuracy middleware"
$shortcut.Save()

Write-Host "Created Phoenix desktop shortcut at $ShortcutPath"
Write-Host "Double-click it to launch Phoenix on port 8003."
