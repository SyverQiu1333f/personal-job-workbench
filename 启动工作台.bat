@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 未找到独立环境，请按照使用说明安装依赖。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" launcher.py %*
if errorlevel 1 (
  echo 启动失败，请查看 logs\startup-error.log
  pause
)
