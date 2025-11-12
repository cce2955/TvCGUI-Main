@echo off
setlocal
set ROOT=%~dp0
set VENV=%ROOT%.venv\Scripts\python.exe

if not exist "%VENV%" (
    echo Virtual environment not found. Run setup.ps1 first.
    pause
    exit /b 1
)

"%VENV%" "%ROOT%main.py" %*
endlocal
