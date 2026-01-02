@echo off
setlocal
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if exist "%PYW%" (
  "%PYW%" "%~dp0app.py"
) else (
  pythonw "%~dp0app.py"
)
