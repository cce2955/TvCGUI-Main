@echo off
setlocal EnableExtensions

rem Dedicated unit-test launcher. This runs the stdlib unittest suite only;
rem it does not start Dolphin, pygame overlays, or the main GUI.
set "ROOT=%~dp0"
set "SETUP=%ROOT%userundotbatinstead.ps1"
set "PYEXE=%ROOT%.venv\Scripts\python.exe"
set "APPDIR=%ROOT%tdp-modules"
set "TEST_RUNNER=%APPDIR%\run_unit_tests.py"

if exist "%PYEXE%" goto HAVE_VENV

echo [run unit tests] Python virtualenv not found at:
echo   "%PYEXE%"

if exist "%SETUP%" goto DO_SETUP

echo [run unit tests] ERROR: setup script is missing at:
echo   "%SETUP%"
echo Extract the full zip or clone the whole repo.
set "ERR=1"
goto END

:DO_SETUP
echo [run unit tests] Running setup script first...
powershell -NoProfile -ExecutionPolicy Bypass -File "%SETUP%"
if errorlevel 1 (
    echo [run unit tests] ERROR: setup script failed with code %errorlevel%.
    set "ERR=%ERRORLEVEL%"
    goto END
)

if not exist "%PYEXE%" (
    echo [run unit tests] ERROR: after setup, venv interpreter still missing:
    echo   "%PYEXE%"
    set "ERR=1"
    goto END
)

:HAVE_VENV
if exist "%TEST_RUNNER%" goto RUN_TESTS

echo [run unit tests] ERROR: Can't find unit-test runner at:
echo   "%TEST_RUNNER%"
set "ERR=1"
goto END

:RUN_TESTS
pushd "%APPDIR%"
set "PYTHONPATH=%APPDIR%"
set "PYTHONUTF8=1"

echo [run unit tests] Using interpreter:
"%PYEXE%" -c "import sys; print(sys.executable)"
echo.
"%PYEXE%" "%TEST_RUNNER%" %*
set "ERR=%ERRORLEVEL%"
popd

:END
if not defined ERR set "ERR=0"
endlocal & exit /b %ERR%
