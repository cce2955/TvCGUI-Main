@echo off
setlocal
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
  set PY=.venv\Scripts\python.exe
) else (
  set PY=python
)
%PY% tvc_chrsel_mdl0_texptr_quicktry.py apply
pause
