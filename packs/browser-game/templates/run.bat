@echo off
setlocal
cd /d "%~dp0"

echo {{NAME}} - dev server
echo.

where npm >nul 2>nul
if errorlevel 1 (
  echo npm was not found. Install Node.js from https://nodejs.org and try again.
  pause
  exit /b 1
)

if not exist "node_modules\" (
  echo Installing dependencies...
  call npm install
  if errorlevel 1 (
    echo npm install failed.
    pause
    exit /b 1
  )
  echo.
)

echo Starting Vite. The project opens in your browser; close this window to stop the server.
echo Add ?seed=42 to the URL to replay an exact level.
echo.
call npm run dev -- --open
pause
