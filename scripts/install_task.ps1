# Requires Windows PowerShell 5.1+ and the ScheduledTasks module.
# Registers a task only; never starts a live check or sends a test email.
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [ValidateLength(1, 200)]
    [ValidatePattern('^[^\\/:*?"<>|\x00-\x1f]+$')]
    [string]$TaskName = 'SNSubmissionMonitor',

    [string]$Config,

    [string]$PythonExecutable,

    [ValidateRange(5, 1440)]
    [int]$IntervalMinutes = 15,

    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-RepositoryFile {
    param([string]$Path, [string]$Description)

    if (-not [System.IO.Path]::IsPathRooted($Path)) {
        $Path = Join-Path $script:RepositoryRoot $Path
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Description was not found: $Path"
    }
    return (Resolve-Path -LiteralPath $Path).ProviderPath
}

function Show-TaskSummary {
    param($Task)

    Write-Host ("Task: {0}; state: {1}; user: {2}" -f $Task.TaskName, $Task.State, $Task.Principal.UserId)
    foreach ($action in $Task.Actions) {
        if ($action.PSObject.Properties.Name -contains 'Execute') {
            Write-Host ("  Executable: {0}" -f $action.Execute)
        }
    }
    # Do not print arbitrary existing action arguments: they may contain secrets.
}

try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'This installer requires Windows. Use --loop or your own scheduler on other systems.'
    }
    if ([string]::IsNullOrWhiteSpace($TaskName)) {
        throw 'TaskName must not be blank.'
    }
    Import-Module ScheduledTasks -ErrorAction Stop
    $script:RepositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name

    # Query exact names instead of treating a user-supplied name as a wildcard.
    $rootTasks = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskPath -eq '\' })
    $existing = @($rootTasks | Where-Object { $_.TaskName -eq $TaskName })
    if ($existing.Count -gt 0) {
        Show-TaskSummary $existing[0]
        if (-not $Force) {
            throw 'This task already exists. Inspect it in Task Scheduler; use -Force explicitly to replace it.'
        }
        Write-Warning 'Only the named task will be replaced. No runtime data will be deleted.'
    }
    if ($TaskName -ne 'NCommsStatusMonitor') {
        $legacy = @($rootTasks | Where-Object { $_.TaskName -eq 'NCommsStatusMonitor' })
        if ($legacy.Count -gt 0) {
            Write-Warning 'Existing NCommsStatusMonitor was left unchanged. Review it separately to avoid duplicate checks.'
        }
    }

    if ([string]::IsNullOrWhiteSpace($Config)) {
        $localConfig = Join-Path $script:RepositoryRoot '.local\config.json'
        $legacyConfig = Join-Path $script:RepositoryRoot 'config.json'
        if (Test-Path -LiteralPath $localConfig -PathType Leaf) {
            $Config = $localConfig
        }
        elseif (Test-Path -LiteralPath $legacyConfig -PathType Leaf) {
            $Config = $legacyConfig
        }
        else {
            throw 'No config found. Run .venv\Scripts\python.exe -m submission_monitor --init first, or pass -Config.'
        }
    }
    $configPath = Resolve-RepositoryFile $Config 'Config file'
    $entryPoint = Resolve-RepositoryFile 'monitor.py' 'Compatibility entry point'

    if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
        $venvPythonw = Join-Path $script:RepositoryRoot '.venv\Scripts\pythonw.exe'
        $venvPython = Join-Path $script:RepositoryRoot '.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $venvPythonw -PathType Leaf) {
            $PythonExecutable = $venvPythonw
        }
        elseif (Test-Path -LiteralPath $venvPython -PathType Leaf) {
            $PythonExecutable = $venvPython
        }
        else {
            throw 'No repository .venv interpreter found. Run setup_windows.bat or pass a full -PythonExecutable path. PATH/Conda interpreters are not selected automatically.'
        }
    }
    $pythonPath = Resolve-RepositoryFile $PythonExecutable 'Python executable'
    if ([System.IO.Path]::GetExtension($pythonPath) -ine '.exe') {
        throw 'PythonExecutable must point to a Python .exe file, not a shell command.'
    }
    $checkPython = $pythonPath
    if ([System.IO.Path]::GetFileName($pythonPath) -ieq 'pythonw.exe') {
        $checkPython = Resolve-RepositoryFile (Join-Path (Split-Path -Parent $pythonPath) 'python.exe') 'Console Python for local validation'
    }

    # Both commands are local-only; neither logs in nor sends notifications.
    # A console interpreter keeps diagnostic output visible even for pythonw tasks.
    Push-Location -LiteralPath $script:RepositoryRoot
    try {
        & $checkPython -I -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
        if ($LASTEXITCODE -ne 0) {
            throw 'Python 3.10 or newer is required.'
        }
        & $checkPython $entryPoint --config $configPath --check-config
        if ($LASTEXITCODE -ne 0) {
            throw 'Local configuration validation failed. Fix the reported configuration before installing a task.'
        }
        & $checkPython $entryPoint --config $configPath --doctor
        if ($LASTEXITCODE -ne 0) {
            throw 'Local runtime diagnostics failed. Fix the reported dependencies or configuration first.'
        }
    }
    finally {
        Pop-Location
    }

    # Task Scheduler launches the executable directly, not through cmd.exe.
    # Resolved Windows file paths cannot contain a double-quote character.
    $arguments = '"{0}" --config "{1}" --once' -f $entryPoint, $configPath
    $action = New-ScheduledTaskAction -Execute $pythonPath -Argument $arguments -WorkingDirectory $script:RepositoryRoot
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
    $executionMinutes = [Math]::Min(10, $IntervalMinutes - 1)
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes $executionMinutes) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
    $description = 'Submission monitor: one check per trigger, logged-in user only. Configuration and credentials remain local.'

    Write-Host ("Python: {0}" -f $pythonPath)
    Write-Host ("Config: {0}" -f $configPath)
    Write-Host ("Interval: {0} minutes; execution cap: {1} minutes; user: {2}" -f $IntervalMinutes, $executionMinutes, $currentUser)
    if ($PSCmdlet.ShouldProcess($TaskName, 'Register logged-in-user scheduled task (no elevation)')) {
        Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description $description -Force:$Force -ErrorAction Stop | Out-Null
        Write-Host 'Task registered. It will run at the next trigger, not immediately.'
        Write-Host 'The computer must be awake, online, and the configured user logged in. No administrator access or Windows password is requested.'
        Write-Host 'Environment-variable overrides must be available to the scheduled-task user; temporary terminal variables are not sufficient.'
        Write-Host 'No other tasks or runtime files were changed.'
    }
}
catch {
    Write-Error ("Task installation failed: {0}" -f $_.Exception.Message) -ErrorAction Continue
    Write-Host 'Do not retry with administrator rights by default. Review the named task, interpreter, config path, and local diagnostics.'
    exit 1
}
