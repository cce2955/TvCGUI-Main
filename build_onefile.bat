@echo off
set PYTHON=C:\Users\cce29\OneDrive\Desktop\TvCGUI-Main\.venv\Scripts\python.exe

cd /d "%~dp0tdp-modules"

echo [build] Working directory: %CD%
%PYTHON% --version
%PYTHON% -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    %PYTHON% -m pip install pyinstaller
)

if exist build   rmdir /s /q build
if exist dist    rmdir /s /q dist

echo [build] Running PyInstaller...
%PYTHON% -m PyInstaller TvCGUI_onefile.spec

if errorlevel 1 (
    echo [build] FAILED.
    pause
    exit /b 1
)

echo [build] SUCCESS: %CD%\dist\TvCGUI.exe
pause