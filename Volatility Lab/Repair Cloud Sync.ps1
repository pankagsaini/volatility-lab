param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$labRoot = $PSScriptRoot
$taskName = 'Volatility Lab Private Drive Sync'
$reportPath = Join-Path $labRoot 'data\google_archive\repair-report.txt'
$messages = [System.Collections.Generic.List[string]]::new()
function Note($text) { Write-Host $text; $messages.Add($text) }
try {
    Note ('Cloud recovery check: ' + (Get-Date -Format o))
    $python = Join-Path $labRoot '.runtime\python.exe'
    $pythonw = Join-Path $labRoot '.runtime\pythonw.exe'
    if (!(Test-Path -LiteralPath $python)) { throw 'Python runtime is missing. Restore the lab runtime before retrying.' }
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (!$CheckOnly) {
        $action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"' + (Join-Path $labRoot 'google_archive_sync.py') + '"') -WorkingDirectory $labRoot
        $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 10)
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
        $principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
        Note 'Windows sync schedule restored: every 10 minutes while signed in.'
        if (!(Get-Process GoogleDriveFS -ErrorAction SilentlyContinue)) {
            $driveExe = Get-ChildItem -LiteralPath 'C:\Program Files\Google\Drive File Stream' -Filter GoogleDriveFS.exe -Recurse -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if ($driveExe) { Start-Process -FilePath $driveExe.FullName -WindowStyle Hidden; Note 'Google Drive started. If it asks, sign in and run repair again.' }
            else { Note 'ACTION: Open/install Google Drive for desktop and sign in.' }
        }
        Note 'Verifying cloud copies, retrying imports and renewing cleanup receipt. Large backlogs can take time.'
        Push-Location $labRoot
        try { & $python (Join-Path $labRoot 'google_archive_sync.py') --verify-receipt; if ($LASTEXITCODE -ne 0) { throw 'Sync failed. Check the guide and Google Drive connection; no cleanup is forced.' } }
        finally { Pop-Location }
    } else { Note ('Windows task: ' + $(if ($task) {$task.State} else {'MISSING - run repair'})) }
    $statusPath = Join-Path $labRoot 'data\google_archive\status.json'
    if (Test-Path -LiteralPath $statusPath) {
        $status = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
        Note ('Local import status: ' + $status.status + '; checked: ' + $status.updated + '; files: ' + $status.total_files)
        if ($status.status -eq 'waiting') { Note 'ACTION: Sign in to Google Drive for desktop, wait for the archive folder, and run repair again.' }
        if ($status.status -eq 'partial') { Note 'ACTION: Some imports failed. Open CLOUD HELP.html; retry after checking Drive. Persistent schema/import failures require technical help.' }
    } else { Note 'No local sync status yet. Run repair.' }
    $receiptPath = Join-Path $labRoot 'data\google_archive\receipt_status.json'
    if (Test-Path -LiteralPath $receiptPath) {
        $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
        Note ('Last local verification receipt: ' + $receipt.verified_at)
        if (((Get-Date).ToUniversalTime() - ([datetimeoffset]$receipt.verified_at).UtcDateTime).TotalDays -gt 8) { Note 'ACTION: Receipt is stale. Run repair with Drive connected; cloud cleanup waits safely.' }
    }
    Note 'This checks local recovery, not Google trigger health. Use CLOUD HELP.html to check the cloud collector.'
    Note 'No market files deleted. Cloud cleanup still requires matching hashes and the 14-day age rule.'
} catch { Note ('ACTION NEEDED: ' + $_.Exception.Message) }
finally {
    New-Item -ItemType Directory -Path (Split-Path $reportPath) -Force | Out-Null
    $messages | Set-Content -LiteralPath $reportPath -Encoding UTF8
    Write-Host ('Report saved: ' + $reportPath)
}
