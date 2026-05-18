@echo off
setlocal EnableExtensions

rem Same test launcher, for people who run it from inside tdp-modules.
set "APPDIR=%~dp0"
set "ROOT=%APPDIR%..\"
set "PYEXE=%ROOT%.venv\Scripts\python.exe"
set "TEST_RUNNER=%APPDIR%run_unit_tests.py"

if not exist "%PYEXE%" (
    echo [run_unit_tests.bat] Missing venv interpreter:
    echo   "%PYEXE%"
    echo Run the root setup script or use the root "run unit tests.bat" launcher.
    exit /b 1
)

pushd "%APPDIR%"
set "PYTHONPATH=%APPDIR%"
set "PYTHONUTF8=1"
"%PYEXE%" "%TEST_RUNNER%" %*
set "ERR=%ERRORLEVEL%"
popd
endlocal & exit /b %ERR%
