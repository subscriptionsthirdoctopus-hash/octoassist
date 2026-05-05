@echo off
REM Double-click entry point for Windows admins. Re-launches as Administrator
REM and runs the OctoAssist server installer.

setlocal

set SCRIPT_DIR=%~dp0
set PSARGS=-NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%Install-OctoAssist-Server.ps1"

REM Test for admin; relaunch elevated if needed.
net session >nul 2>&1
if %errorlevel% NEQ 0 (
    powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '%PSARGS%'"
    exit /b
)

powershell %PSARGS%
pause
