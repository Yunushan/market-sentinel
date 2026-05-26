@echo off
setlocal

set "ROOT=%~dp0"
if "%API_PORT%"=="" set "API_PORT=8765"

if exist "%ROOT%frontend\dist\index.html" (
  echo Starting built React GUI on http://127.0.0.1:%API_PORT%
  call "%ROOT%run_web_gui_prod.bat"
  exit /b %errorlevel%
)

if exist "%ROOT%frontend\node_modules" (
  echo Starting React dev GUI on http://127.0.0.1:5173
  call "%ROOT%run_web_gui_dev.bat"
  exit /b %errorlevel%
)

echo React GUI is not built and frontend dependencies are not installed.
echo.
echo Build production assets with:
echo   build_web_gui.bat
echo.
echo Or install dependencies for the Vite dev server:
echo   cd frontend
echo   npm install
echo   npm run dev
echo.
echo Tkinter fallback remains available:
echo   run_gui.bat
echo   python app.py
pause
exit /b 1
