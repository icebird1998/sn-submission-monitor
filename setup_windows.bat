@echo off
setlocal
set "USAGE_EXIT=0"
rem Opt-in dependency setup only. No login, email, monitoring, or task changes.
if /i "%~1"=="--help" goto usage
if /i "%~1"=="/?" goto usage
if not "%~2"=="" goto usage_error

set "VENV_DIR=%~dp0.venv"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
echo This installs Python dependencies in the repository .venv.
echo It does not create config, log in, send email, schedule tasks, or download a browser.

if exist "%VENV_PY%" goto environment_ready
if exist "%VENV_DIR%\" (
    echo [ERROR] .venv exists without Scripts\python.exe. Inspect it manually; nothing was deleted.
    exit /b 1
)
if not "%~1"=="" goto explicit_python

where py.exe >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python launcher not found. Install Python 3.10+ or pass its full executable path.
    echo Example: setup_windows.bat "C:\Path\Python312\python.exe"
    exit /b 1
)
py -3 -I -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 goto version_error
py -3 -m venv "%VENV_DIR%"
if errorlevel 1 goto setup_failed
goto environment_ready

:explicit_python
if not exist "%~f1" (
    echo [ERROR] Python executable not found. Pass a full path, not a command name.
    exit /b 1
)
"%~f1" -I -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 goto version_error
"%~f1" -m venv "%VENV_DIR%"
if errorlevel 1 goto setup_failed

:environment_ready
"%VENV_PY%" -I -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 goto version_error
"%VENV_PY%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto setup_failed
"%VENV_PY%" -m pip install -e "%~dp0." --no-deps
if errorlevel 1 goto setup_failed

echo.
echo [OK] Dependencies and the editable console entry point are installed.
echo Next, run these commands from the repository folder:
echo   .venv\Scripts\python.exe -m submission_monitor --init
echo   .venv\Scripts\python.exe -m submission_monitor --check-config
echo For SN or EM only, install their required browser before diagnostics:
echo   .venv\Scripts\python.exe -m playwright install chromium
echo MTS-only monitoring does not need a browser download.
echo Then check local runtime health:
echo   .venv\Scripts\python.exe -m submission_monitor --doctor
echo Inspect README.md or README.en.md before your first live check or task installation.
exit /b 0

:version_error
echo [ERROR] Python 3.10 or newer is required. No configuration or runtime data was changed.
exit /b 1

:setup_failed
echo [ERROR] Setup did not finish. Review the command error above and retry after fixing it.
echo Any existing config, credentials, runtime data, and scheduled tasks were left unchanged.
exit /b 1

:usage_error
set "USAGE_EXIT=1"
echo [ERROR] Unexpected arguments.
:usage
echo Usage: setup_windows.bat ["C:\Full\Path\To\python.exe"]
echo Reuses an existing repository .venv, otherwise uses the supplied Python or the Windows py -3 launcher.
echo No interpreter is selected from a random Conda environment.
exit /b %USAGE_EXIT%
