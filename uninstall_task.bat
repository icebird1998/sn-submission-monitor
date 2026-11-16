@echo off
setlocal
rem Inspects and confirms the named task. Never deletes runtime data.
rem Example: uninstall_task.bat -TaskName SNSubmissionMonitor
where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Windows PowerShell was not found.
    exit /b 1
)
if not exist "%~dp0scripts\uninstall_task.ps1" (
    echo [ERROR] scripts\uninstall_task.ps1 was not found. Use the complete repository.
    exit /b 1
)
powershell.exe -NoLogo -NoProfile -File "%~dp0scripts\uninstall_task.ps1" %*
exit /b %errorlevel%
