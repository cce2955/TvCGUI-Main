@echo off
setlocal EnableExtensions

rem Live Dolphin smoke diagnostics. Read-only by default. Optional write echo:
rem   "run Dolphin smoke tests.bat" --write-echo 0x90000000
set "ROOT=%~dp0"
set "SETUP=%ROOT%userundotbatinstead.ps1"
set "PYEXE=%ROOT%.venv\Scripts\python.exe"
set "APPDIR=%ROOT%tdp-modules"
set "SMOKE_RUNNER=%APPDIR%\run_dolphin_smoke_tests.py"

if exist "%PYEXE%" goto HAVE_VENV

echo [Dolphin smoke] Python virtualenv not found at:
echo   "%PYEXE%"

if exist "%SETUP%" goto DO_SETUP

echo [Dolphin smoke] ERROR: setup script is missing at:
echo   "%SETUP%"
echo Extract the full zip or clone the whole repo.
set "ERR=1"
goto END

:DO_SETUP
echo [Dolphin smoke] Running setup script first...
powershell -NoProfile -ExecutionPolicy Bypass -File "%SETUP%"
if errorlevel 1 (
    echo [Dolphin smoke] ERROR: setup script failed with code %errorlevel%.
    set "ERR=%ERRORLEVEL%"
    goto END
)

if not exist "%PYEXE%" (
    echo [Dolphin smoke] ERROR: after setup, venv interpreter still missing:
    echo   "%PYEXE%"
    set "ERR=1"
    goto END
)

:HAVE_VENV
if exist "%SMOKE_RUNNER%" goto RUN_SMOKE

echo [Dolphin smoke] ERROR: Can't find smoke runner at:
echo   "%SMOKE_RUNNER%"
set "ERR=1"
goto END

:RUN_SMOKE
pushd "%APPDIR%"
set "PYTHONPATH=%APPDIR%"
set "PYTHONUTF8=1"

echo [Dolphin smoke] Using interpreter:
"%PYEXE%" -c "import sys; print(sys.executable)"
echo.
"%PYEXE%" "%SMOKE_RUNNER%" %*
set "ERR=%ERRORLEVEL%"
popd

:END
if not defined ERR set "ERR=0"
endlocal & exit /b %ERR%
