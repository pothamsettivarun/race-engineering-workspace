@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.11 or newer and enable Add Python to PATH.
  pause
  exit /b 1
)
python -c "import numpy, yaml, irsdk" >nul 2>nul
if errorlevel 1 (
  echo Installing telemetry dependencies...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Dependency installation failed. Run python -m pip install -r requirements.txt manually.
    pause
    exit /b 1
  )
)
python server.py --open
pause
