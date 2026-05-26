@echo off
setlocal

set "ROOT=%~dp0"
if "%API_PORT%"=="" set "API_PORT=8765"

if exist "%ROOT%.venv\Scripts\python.exe" (
  set "PYTHON_CMD=""%ROOT%.venv\Scripts\python.exe"""
) else (
  set "PYTHON_CMD=python"
)

if not exist "%ROOT%frontend\dist\index.html" (
  echo React production build is missing.
  echo Build it with:
  echo   build_web_gui.bat
  echo.
  echo Or manually:
  echo   cd frontend
  echo   npm install
  echo   npm run build
  echo.
  echo Tkinter fallback remains available:
  echo   run_gui.bat
  pause
  exit /b 1
)

cd /d "%ROOT%"
echo React production GUI: http://127.0.0.1:%API_PORT%
echo Serving: %ROOT%frontend\dist
echo Tkinter fallback: run_gui.bat
%PYTHON_CMD% web_api.py --host 127.0.0.1 --port %API_PORT% --frontend-dir "%ROOT%frontend\dist"
