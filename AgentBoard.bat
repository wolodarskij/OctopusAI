@echo off
rem Open the Agent Board of every registered project: run AgentBoard.bat. Ctrl+C in this window stops it.
rem `AgentBoard.bat --project <name|path>` opens the page on that project. Run from an empty
rem folder, or given a folder in no project, it installs Octopus AI there first; with no project
rem registered and none given, it asks for the folder.
rem When Python 3.10 or newer, its tkinter, or git is missing, it runs tools\setup-windows.ps1 first.
title Octopus AI - Agent Board
call :ready && goto board
echo Octopus AI needs Python 3.10 or newer and git. The setup installs what is missing.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup-windows.ps1" -Yes -Board
call :refresh
call :ready || goto failed
:board
python "%~dp0octopus.py" board %*
if errorlevel 1 pause
exit /b

:ready
python -c "import sys, tkinter; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 || exit /b 1
git --version >nul 2>&1 || exit /b 1
exit /b 0

rem The PATH that the setup wrote, for this window.
:refresh
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%p"
exit /b 0

:failed
echo.
echo Python or git is still missing. Fix each line marked !! above, then start AgentBoard.bat again.
pause
exit /b 1
