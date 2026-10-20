@echo off
rem 删除监控计划任务
chcp 65001 >nul
schtasks /Delete /F /TN "SNSubmissionMonitor"
pause
