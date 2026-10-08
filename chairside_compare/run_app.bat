@echo off
cd /d "%~dp0"
"..\.venv\Scripts\python.exe" run.py %*
if errorlevel 1 pause
