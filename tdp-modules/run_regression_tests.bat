@echo off
setlocal
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1

set "PYTHON_EXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%~dp0..\.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0..\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%~dp0venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%~dp0..\venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0..\venv\Scripts\python.exe"

if defined PYTHON_EXE (
  echo [regression] Using interpreter: %PYTHON_EXE%
  "%PYTHON_EXE%" run_regression_tests.py
) else (
  echo [regression] Project virtual environment not found. Falling back to py -3.
  echo [regression] Checked .venv and venv in both tdp-modules and its parent directory.
  py -3 run_regression_tests.py
)

set EXITCODE=%ERRORLEVEL%
echo.
if not "%EXITCODE%"=="0" (
  echo Regression gate FAILED. Do not package this build until the failure is reviewed.
) else (
  echo Regression gate PASSED.
)
exit /b %EXITCODE%
