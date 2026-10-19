# Stops the background organizer and removes it from startup.
# Run:  powershell -ExecutionPolicy Bypass -File scripts\uninstall_autostart.ps1
$TaskName = "ML Download Organizer"

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "Removed '$TaskName' from startup."
