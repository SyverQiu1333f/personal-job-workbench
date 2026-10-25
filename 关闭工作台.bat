@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "runtime\python.exe" (
  "runtime\python.exe" -X utf8 portable.py stop %*
) else (
  ".venv\Scripts\python.exe" -X utf8 portable.py stop %*
)
if errorlevel 1 pause
