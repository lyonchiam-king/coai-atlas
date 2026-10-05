@echo off
REM COAI Atlas one-time setup: double-click this once. All the work is in setup.ps1.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 pause
