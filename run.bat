@echo off
setlocal EnableExtensions

rem === Resolve paths ===
set "ROOT=%~dp0"

rem your setup script in the same folder as this .bat
set "SETUP=%ROOT%userundotbatinstead.ps1"

rem venv python
set "PYEXE=%ROOT%.venv\Scripts\python.exe"

rem app
set "APPDIR=%ROOT%tdp-modules"
set "MAIN=%APPDIR%\main.py"

rem -------------------------------------------------
rem Step 1: ensure venv exists (if not, run setup)
rem -------------------------------------------------
if exist "%PYEXE%" goto HAVE_VENV

echo [run.bat] Python virtualenv not found at:
echo   "%PYEXE%"

if exist "%SETUP%" goto DO_SETUP

echo [run.bat] ERROR: setup script is missing at:
echo   "%SETUP%"
echo Extract the full zip or clone the whole repo.
set "ERR=1"
goto END

:DO_SETUP
echo [run.bat] Running setup script (this may take a few minutes)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%SETUP%"
if errorlevel 1 (
    echo [run.bat] ERROR: setup script failed with code %errorlevel%.
    set "ERR=%ERRORLEVEL%"
    goto END
)

if not exist "%PYEXE%" (
    echo [run.bat] ERROR: after setup, venv interpreter still missing:
    echo   "%PYEXE%"
    set "ERR=1"
    goto END
)

:HAVE_VENV

rem -------------------------------------------------
rem Step 2: check main.py exists
rem -------------------------------------------------
if exist "%MAIN%" goto RUN_APP

echo [run.bat] ERROR: Can't find main.py at:
echo   "%MAIN%"
set "ERR=1"
goto END

:RUN_APP
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

:END
if not defined ERR set "ERR=0"
endlocal & exit /b %ERR%
