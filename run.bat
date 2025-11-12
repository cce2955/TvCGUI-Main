@echo off
setlocal

rem Folder containing this .bat (with trailing backslash)
set "ROOT=%~dp0"

rem App directory where main.py, portraits, CSVs live
set "APPDIR=%ROOT%tdp-modules"

rem Use the venv we created in project root
set "VENV=%ROOT%.venv\Scripts\python.exe"

if not exist "%VENV%" (
  echo Virtual environment not found. Run setup.ps1 first.
  pause
  exit /b 1
)

rem Ensure relative paths resolve like when you run python manually inside tdp-modules
pushd "%APPDIR%"

rem Optional: help Python find your modules if you import local packages
set "PYTHONPATH=%APPDIR%"

rem Optional: better default encoding
set "PYTHONUTF8=1"

"%VENV%" "%APPDIR%\main.py" %*
set ERR=%ERRORLEVEL%

popd
exit /b %ERR%
