@echo off
setlocal

set "ROOT=%~dp0"

where npm >nul 2>&1
if errorlevel 1 (
  echo Node.js/npm was not found on PATH.
  echo Install Node.js before building the React GUI.
  echo Tkinter fallback remains available:
  echo   run_gui.bat
  pause
  exit /b 1
)

cd /d "%ROOT%frontend"
if not exist node_modules (
  echo Installing frontend dependencies...
  npm install
  if errorlevel 1 (
    echo npm install failed.
    pause
    exit /b 1
  )
)

echo Building React production assets...
npm run build
if errorlevel 1 (
  echo React build failed.
  pause
  exit /b 1
)

echo Build complete. Start the production GUI with:
echo   run_web_gui_prod.bat
pause
