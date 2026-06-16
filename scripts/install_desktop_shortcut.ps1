# Create a "Phoenix Cognition" desktop shortcut (Phase 13 Step 5c).
# Targets the launcher (which starts the daemon if needed + opens the control
# panel) and uses the custom phoenix-cognition.ico. Re-runnable (overwrites).

$repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$desktop = [Environment]::GetFolderPath('Desktop')
$lnkPath = Join-Path $desktop "Phoenix Cognition.lnk"
$launcher = Join-Path $repo "scripts\phoenix_cognition_launch.ps1"
$icon = Join-Path $repo "phoenix\ui\static\phoenix-cognition.ico"
$pwsh = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

if (-not (Test-Path $icon)) {
    Write-Error "icon not found: $icon  (run: python scripts/gen_app_icon.py)"
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnkPath)
$sc.TargetPath = $pwsh
$sc.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcher`""
$sc.WorkingDirectory = $repo
$sc.IconLocation = "$icon,0"
$sc.Description = "Open the Phoenix Cognition control panel"
$sc.WindowStyle = 7  # minimized launcher window
$sc.Save()

Write-Output "created shortcut: $lnkPath"
Write-Output "  -> $pwsh -File $launcher"
Write-Output "  icon: $icon"
