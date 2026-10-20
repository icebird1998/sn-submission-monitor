@echo off
rem 一键创建 Windows 计划任务：每 15 分钟静默检查一次
rem 用法：双击本文件，或在 cmd 中运行 install_task.bat
chcp 65001 >nul
setlocal

set SCRIPT_DIR=%~dp0
if not exist "%SCRIPT_DIR%config.json" (
    echo [错误] 未找到 config.json，请先复制 config.example.json 为 config.json 并填写。
    pause
    exit /b 1
)

rem 找 pythonw（无窗口运行）
set PYW=
for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined PYW set PYW=%%i
if not defined PYW (
    echo [错误] 未找到 pythonw，请先安装 Python 并加入 PATH。
    pause
    exit /b 1
)

schtasks /Create /F /TN "SNSubmissionMonitor" /SC MINUTE /MO 15 ^
  /TR "\"%PYW%\" \"%SCRIPT_DIR%monitor.py\" --config \"%SCRIPT_DIR%config.json\""
if %errorlevel%==0 (
    echo.
    echo [成功] 计划任务 SNSubmissionMonitor 已创建，每 15 分钟检查一次。
    echo        日志: %SCRIPT_DIR%monitor.log
    echo        删除: 双击 uninstall_task.bat
) else (
    echo [失败] 计划任务创建失败，请以管理员身份重试。
)
pause
