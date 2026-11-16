@echo off
setlocal
rem Forward explicit options to the safe logged-in-user task installer.
rem Example: install_task.bat -TaskName SNSubmissionMonitor -IntervalMinutes 15
where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Windows PowerShell was not found.
    exit /b 1
)
if not exist "%~dp0scripts\install_task.ps1" (
    echo [ERROR] scripts\install_task.ps1 was not found. Use the complete repository.
    exit /b 1
)
powershell.exe -NoLogo -NoProfile -File "%~dp0scripts\install_task.ps1" %*
exit /b %errorlevel%
