@echo off
setlocal EnableExtensions

rem === Resolve paths ===
set "ROOT=%~dp0"
rem strip trailing backslash if present
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "SETUP=%ROOT%\userundotbatinstead.ps1"
set "PYEXE=%ROOT%\.venv\Scripts\python.exe"
set "APPDIR=%ROOT%\tdp-modules"
set "MAIN=%APPDIR%\main.py"

rem === Ensure venv exists, otherwise run setup.ps1 ===
if not exist "%PYEXE%" (
  if not exist "%SETUP%" (
    echo ERROR: Missing virtualenv and setup.ps1 not found at "%SETUP%".
    echo Make sure you extracted all files from the zip.
    exit /b 1
  )

  echo Python virtual environment not found. Running setup.ps1 ...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%SETUP%"
  if errorlevel 1 (
    echo setup.ps1 failed (exit code %errorlevel%). Aborting.
    exit /b %errorlevel%
  )
)

rem === Re-check venv and main.py after setup ===
if not exist "%PYEXE%" (
  echo ERROR: After running setup.ps1, venv interpreter still missing:
  echo        "%PYEXE%"
  exit /b 1
)

if not exist "%MAIN%" (
  echo ERROR: Can't find main.py at "%MAIN%"
  exit /b 1
)

rem === Run the app ===
pushd "%APPDIR%"
set "PYTHONPATH=%APPDIR%"
set "PYTHONUTF8=1"

echo Using interpreter:
"%PYEXE%" -c "import sys; print(sys.executable)"
echo Import check:
"%PYEXE%" -c "import pygame,sys; print('pygame', pygame.__version__)"

echo.
"%PYEXE%" "%MAIN%" %*
set "ERR=%ERRORLEVEL%"

popd
exit /b %ERR%
