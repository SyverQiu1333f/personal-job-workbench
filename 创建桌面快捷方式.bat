@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "runtime\pythonw.exe" (
  start "" "runtime\pythonw.exe" -X utf8 portable.py shortcut --gui
) else (
  start "" ".venv\Scripts\pythonw.exe" -X utf8 portable.py shortcut --gui
)
