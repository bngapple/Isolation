@echo off
setlocal ENABLEDELAYEDEXPANSION

set BUNDLE_DIR=%~dp0
set REPO_DIR=%BUNDLE_DIR%..
set PY_APP=%BUNDLE_DIR%python_app
set NT_TARGET=%USERPROFILE%\Documents\NinjaTrader 8\bin\Custom\Strategies
set VENV_DIR=%PY_APP%\.venv

echo ========================================
echo Executable_PC_Bundle - Update and Run
echo ========================================

where git >nul 2>nul
if %ERRORLEVEL%==0 (
  pushd "%REPO_DIR%"
  echo Checking for repo updates...
  git fetch origin
  git pull --ff-only origin main
  popd
) else (
  echo Git not found - skipping update check.
)

echo.
echo Stopping old Python bridge process on port 5001 if present...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -LocalPort 5001 -State Listen -ErrorAction SilentlyContinue ^| Select-Object -ExpandProperty OwningProcess -Unique ^| ForEach-Object { try { Stop-Process -Id $_ -Force } catch {} }"

echo.
echo Rebuilding Python virtual environment...
if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"
python -m venv "%VENV_DIR%"
if not exist "%VENV_DIR%\Scripts\python.exe" (
  echo Failed to create virtual environment.
  exit /b 1
)

echo.
echo Installing Python dependencies...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
"%VENV_DIR%\Scripts\python.exe" -m pip install -r "%PY_APP%\requirements.txt"

echo.
echo Copying NinjaTrader strategy files...
if not exist "%NT_TARGET%" mkdir "%NT_TARGET%"
if exist "%NT_TARGET%\PythonBridge.cs" del /f /q "%NT_TARGET%\PythonBridge.cs"
if exist "%NT_TARGET%\Rsi4060Standalone.cs" del /f /q "%NT_TARGET%\Rsi4060Standalone.cs"
copy /y "%BUNDLE_DIR%ninjatrader\PythonBridge.cs" "%NT_TARGET%\PythonBridge.cs" >nul
copy /y "%BUNDLE_DIR%ninjatrader\Rsi4060Standalone.cs" "%NT_TARGET%\Rsi4060Standalone.cs" >nul

echo.
echo Starting Python bridge in a new window...
start "Isolation Python Bridge" cmd /k "cd /d "%PY_APP%" && "%VENV_DIR%\Scripts\python.exe" main.py"

echo.
echo Done.
echo.
echo Next steps in NinjaTrader:
echo 1. Open NinjaScript Editor
echo 2. Compile PythonBridge.cs
echo 3. Compile Rsi4060Standalone.cs
echo 4. Attach PythonBridge to the chart/account you want to use
echo 5. Confirm the bridge connects on port 5001
echo.
pause
