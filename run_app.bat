@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" chairside_compare\run.py %*
if errorlevel 1 pause
