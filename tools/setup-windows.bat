@echo off
rem Set up Windows to run Octopus AI: run tools\setup-windows.bat.
rem   tools\setup-windows.bat                           ask before each install
rem   tools\setup-windows.bat -Yes                      install whatever is missing without asking
rem   tools\setup-windows.bat -Providers claude,codex   these AI providers' command-line tools: claude, codex
title Octopus AI - setup
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-windows.ps1" %*
set CODE=%ERRORLEVEL%
rem A run from Explorer opens a window that closes at the end: keep it open to read the result.
echo %cmdcmdline% | "%SystemRoot%\System32\find.exe" /i "%~0" >nul && pause
exit /b %CODE%
