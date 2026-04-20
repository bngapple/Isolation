@echo off
setlocal

set SCRIPT_DIR=%~dp0
set PY_APP=%SCRIPT_DIR%..\python_app
set VENV_PY=%PY_APP%\.venv\Scripts\python.exe

cd /d "%PY_APP%"

if exist "%VENV_PY%" (
  "%VENV_PY%" main.py
) else (
  python main.py
)
