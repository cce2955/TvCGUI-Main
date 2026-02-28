@echo off
setlocal EnableExtensions

rem === Resolve paths ===
set "ROOT=%~dp0"
set "SETUP=%ROOT%userundotbatinstead.ps1"
set "PYEXE=%ROOT%.venv\Scripts\python.exe"
set "APPDIR=%ROOT%tdp-modules"
set "MAIN=%APPDIR%\hitboxesscaling.py"

rem -------------------------------------------------
rem Step 1: ensure venv exists (if not, run setup)
rem -------------------------------------------------
if exist "%PYEXE%" goto HAVE_VENV

echo [hitbox.bat] Python virtualenv not found at:
echo   "%PYEXE%"

if exist "%SETUP%" goto DO_SETUP

echo [hitbox.bat] ERROR: setup script is missing at:
echo   "%SETUP%"
echo Extract the full zip or clone the whole repo.
set "ERR=1"
goto END

:DO_SETUP
echo [hitbox.bat] Running setup script (this may take a few minutes)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%SETUP%"
if errorlevel 1 (
    echo [hitbox.bat] ERROR: setup script failed with code %errorlevel%.
    set "ERR=%ERRORLEVEL%"
    goto END
)

if not exist "%PYEXE%" (
    echo [hitbox.bat] ERROR: after setup, venv interpreter still missing:
    echo   "%PYEXE%"
    set "ERR=1"
    goto END
)

:HAVE_VENV

if not exist "%MAIN%" (
    echo [hitbox.bat] ERROR: Can't find hitboxesscaling.py at:
    echo   "%MAIN%"
    set "ERR=1"
    goto END
)

pushd "%APPDIR%"
set "PYTHONPATH=%APPDIR%"
set "PYTHONUTF8=1"

echo [hitbox.bat] Launching hitbox overlay...
"%PYEXE%" "%MAIN%" %*
set "ERR=%ERRORLEVEL%"

popd

:END
if not defined ERR set "ERR=0"
endlocal & exit /b %ERR%