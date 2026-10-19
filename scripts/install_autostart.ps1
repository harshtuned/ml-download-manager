# Starts the download organizer in the background every time you log in.
# Run once:  powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
$ErrorActionPreference = "Stop"

$TaskName = "ML Download Organizer"
$Project  = Split-Path -Parent $PSScriptRoot
$Pythonw  = Join-Path $Project "venv\Scripts\pythonw.exe"   # pythonw = no console window
$Script   = Join-Path $Project "app\run.py"
$User     = "$env:USERDOMAIN\$env:USERNAME"

if (-not (Test-Path $Pythonw)) { throw "Not found: $Pythonw (create the venv first)" }

$action  = New-ScheduledTaskAction -Execute "`"$Pythonw`"" -Argument "`"$Script`"" -WorkingDirectory $Project
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$trigger.Delay = "PT30S"    # give drives and the network a moment after login

# Task Scheduler defaults would stop it on battery and kill it after 3 days.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Installed '$TaskName' - it is running now and will start at every login."
Write-Host "Log file: $Project\data\organizer.log"
