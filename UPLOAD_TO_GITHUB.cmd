@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\upload-github.ps1"
if errorlevel 1 echo Upload failed. Read the error above; main was not directly updated.
pause
