@echo off
setlocal EnableExtensions

rem === paths ===
set "ROOT=%~dp0"
set "PYEXE=%ROOT%.venv\Scripts\python.exe"
set "APPDIR=%ROOT%tdp-modules"

if not exist "%PYEXE%" (
  echo ERROR: Missing venv interpreter: "%PYEXE%"
  echo Run setup.ps1 first.
  exit /b 1
)

if not exist "%APPDIR%\main.py" (
  echo ERROR: Can't find main.py at "%APPDIR%\main.py"
  exit /b 1
)

rem === keep CWD in app dir so relative assets (portraits/csv) resolve ===
pushd "%APPDIR%"

rem === optional helpers ===
set "PYTHONPATH=%APPDIR%"
set "PYTHONUTF8=1"

echo Using interpreter:
"%PYEXE%" -c "import sys; print(sys.executable)"
echo Import check:
"%PYEXE%" -c "import pygame,sys; print('pygame', pygame.__version__)"

echo.
"%PYEXE%" "%APPDIR%\main.py" %*
set "ERR=%ERRORLEVEL%"

popd
exit /b %ERR%
