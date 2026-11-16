# Requires Windows PowerShell 5.1+ and the ScheduledTasks module.
# Removes only an explicitly inspected/confirmed task; never removes data.
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [ValidateLength(1, 200)]
    [ValidatePattern('^[^\\/:*?"<>|\x00-\x1f]+$')]
    [string]$TaskName = 'SNSubmissionMonitor',

    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'This uninstaller requires Windows.'
    }
    if ([string]::IsNullOrWhiteSpace($TaskName)) {
        throw 'TaskName must not be blank.'
    }
    Import-Module ScheduledTasks -ErrorAction Stop
    $rootTasks = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskPath -eq '\' })
    $matchingTasks = @($rootTasks | Where-Object { $_.TaskName -eq $TaskName })
    if ($matchingTasks.Count -eq 0) {
        Write-Host ("No task named '{0}' was found. Nothing was changed." -f $TaskName)
        if ($TaskName -ne 'NCommsStatusMonitor' -and @($rootTasks | Where-Object { $_.TaskName -eq 'NCommsStatusMonitor' }).Count -gt 0) {
            Write-Host 'Legacy NCommsStatusMonitor was left unchanged. Inspect it separately and pass -TaskName NCommsStatusMonitor only if you intend to remove it.'
        }
        exit 0
    }
    $task = $matchingTasks[0]
    Write-Host ("Task: {0}; state: {1}; user: {2}" -f $task.TaskName, $task.State, $task.Principal.UserId)
    foreach ($action in $task.Actions) {
        if ($action.PSObject.Properties.Name -contains 'Execute') {
            Write-Host ("  Executable: {0}" -f $action.Execute)
        }
    }
    # Existing action arguments can contain secrets, so do not echo them.
    Write-Host 'Only this task registration will be removed. Config, credentials, baselines, queued notifications, logs, browser sessions, and the virtual environment will remain.'

    if (-not $Force -and -not $WhatIfPreference) {
        $answer = Read-Host ("Type the exact task name '{0}' to confirm removal (anything else cancels)" -f $TaskName)
        if ($answer -cne $TaskName) {
            Write-Host 'Cancelled. Nothing was removed.'
            exit 0
        }
    }
    if ($PSCmdlet.ShouldProcess($TaskName, 'Unregister this scheduled task only')) {
        Unregister-ScheduledTask -InputObject $task -Confirm:$false -ErrorAction Stop
        Write-Host 'Task registration removed. No runtime data or other tasks were deleted.'
    }
}
catch {
    Write-Error ("Task removal failed: {0}" -f $_.Exception.Message) -ErrorAction Continue
    exit 1
}
