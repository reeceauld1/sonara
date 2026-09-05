@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" "dev.py"
if errorlevel 1 pause
