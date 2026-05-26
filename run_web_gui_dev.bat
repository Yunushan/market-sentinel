@echo off
setlocal

set "ROOT=%~dp0"
if "%API_PORT%"=="" set "API_PORT=8765"

if exist "%ROOT%.venv\Scripts\python.exe" (
  set "PYTHON_CMD=""%ROOT%.venv\Scripts\python.exe"""
) else (
  set "PYTHON_CMD=python"
)

where npm >nul 2>&1
if errorlevel 1 (
  echo Node.js/npm was not found on PATH.
  echo Install Node.js, or use the Tkinter fallback:
  echo   run_gui.bat
  pause
  exit /b 1
)

if not exist "%ROOT%frontend\node_modules" (
  echo Frontend dependencies are not installed.
  echo Run:
  echo   cd frontend
  echo   npm install
  echo.
  echo Tkinter fallback remains available:
  echo   run_gui.bat
  pause
  exit /b 1
)

start "Prediction Market API" cmd /k "cd /d ""%ROOT%"" && %PYTHON_CMD% web_api.py --host 127.0.0.1 --port %API_PORT%"

cd /d "%ROOT%frontend"
set "VITE_API_BASE_URL=http://127.0.0.1:%API_PORT%"
echo React dev GUI: http://127.0.0.1:5173
echo Python API: http://127.0.0.1:%API_PORT%
echo Tkinter fallback: run_gui.bat
npm run dev
