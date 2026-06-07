@echo off
setlocal
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe tvc_chrsel_thumbnail_readonly_dump.py
) else (
  python tvc_chrsel_thumbnail_readonly_dump.py
)
pause
