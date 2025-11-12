#Requires -Version 5.1
$ErrorActionPreference = 'Stop'

$Root   = Split-Path -Parent $MyInvocation.MyCommand.Path
$PyExe  = Join-Path $Root '.venv\Scripts\python.exe'
$AppDir = Join-Path $Root 'tdp-modules'
$Main   = Join-Path $AppDir 'main.py'

if (-not (Test-Path $PyExe)) { throw "Missing venv interpreter: $PyExe (run setup.ps1)" }
if (-not (Test-Path $Main))  { throw "Can't find main.py at $Main" }

Push-Location $AppDir
$env:PYTHONPATH = $AppDir
$env:PYTHONUTF8 = '1'

Write-Host "Using interpreter:"
& $PyExe -c "import sys; print(sys.executable)"
Write-Host "Import check:"
& $PyExe -c "import pygame,sys; print('pygame', pygame.__version__)"

& $PyExe $Main @args
$code = $LASTEXITCODE
Pop-Location
exit $code
