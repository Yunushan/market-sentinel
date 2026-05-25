@echo off
setlocal
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PYW%" if exist "%PY%" (
  "%PY%" --version >nul 2>&1
  if not errorlevel 1 (
    "%PYW%" "%~dp0app.py"
    exit /b
  )
)

where pyw >nul 2>&1
if not errorlevel 1 (
  pyw -3.14 "%~dp0app.py"
  if not errorlevel 1 exit /b
  pyw -3 "%~dp0app.py"
  if not errorlevel 1 exit /b
)

pythonw "%~dp0app.py"
