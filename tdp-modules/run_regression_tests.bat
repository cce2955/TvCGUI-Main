@echo off
setlocal
cd /d "%~dp0"
py -3 run_regression_tests.py
set EXITCODE=%ERRORLEVEL%
echo.
if not "%EXITCODE%"=="0" (
  echo Regression gate FAILED. Do not package this build until the failure is reviewed.
) else (
  echo Regression gate PASSED.
)
exit /b %EXITCODE%
