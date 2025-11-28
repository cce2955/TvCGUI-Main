@echo off
setlocal EnableExtensions

rem === Resolve paths ===
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "SETUP=%ROOT%\userundotbatinstead.ps1"
set "PYEXE=%ROOT%\.venv\Scripts\python.exe"
set "APPDIR=%ROOT%\tdp-modules"
set "MAIN=%APPDIR%\main.py"

rem === If venv is missing, run setup.ps1 as an installer ===
if not exist "%PYEXE%" (
  echo [run.bat] Python virtualenv not found at:
  echo   "%PYEXE%"
  if not exist "%SETUP%" (
    echo [run.bat] ERROR: setup.ps1 is also missing at:
    echo   "%SETUP%"
    echo Extract the full zip, or git clone the whole repo.
    exit /b 1
  )

  echo [run.bat] Running setup.ps1 (this may take a few minutes)...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%SETUP%"
  if errorlevel 1 (
    echo [run.bat] ERROR: setup.ps1 failed with code %errorlevel%.
    exit /b %errorlevel%
  )
)

rem === Recheck venv / main after setup ===
if not exist "%PYEXE%" (
  echo [run.bat] ERROR: venv interpreter still missing:
  echo   "%PYEXE%"
  exit /b 1
)

if not exist "%MAIN%" (
  echo [run.bat] ERROR: Can't find main.py at:
  echo   "%MAIN%"
  exit /b 1
)

rem === Launch the app ===
pushd "%APPDIR%"
set "PYTHONPATH=%APPDIR%"
set "PYTHONUTF8=1"

echo [run.bat] Using interpreter:
"%PYEXE%" -c "import sys; print(sys.executable)"
echo [run.bat] Import check:
"%PYEXE%" -c "import pygame,sys; print('pygame', pygame.__version__)"

echo.
"%PYEXE%" "%MAIN%" %*
set "ERR=%ERRORLEVEL%"

popd
exit /b %ERR%
