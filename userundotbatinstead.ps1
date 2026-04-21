# setup.ps1
# Run with: Right-click → Run with PowerShell (or: powershell -ExecutionPolicy Bypass -File .\setup.ps1)

param(
    [string]$PythonMinVersion = "3.10"
)

function Write-Info($msg) {
    Write-Host $msg
}

function Fail($msg) {
    Write-Error $msg
    exit 1
}

# 1. Resolve project root (where this script lives)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Info "TvC Tool setup starting in $ScriptDir"

# 2. Find python
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
    if ($python) {
        $pythonCmd = "py"
    } else {
        Fail "Python was not found in PATH. Install Python 3 and re-run setup.ps1."
    }
} else {
    $pythonCmd = "python"
}

# 3. Check python version
$verOut = & $pythonCmd -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}.{v.micro}')"
if (-not $verOut) {
    Fail "Unable to detect Python version."
}

function VersionLessThan($a, $b) {
    $aParts = $a.Split(".") | ForEach-Object { [int]$_ }
    $bParts = $b.Split(".") | ForEach-Object { [int]$_ }
    for ($i = 0; $i -lt [Math]::Max($aParts.Count, $bParts.Count); $i++) {
        $av = $(if ($i -lt $aParts.Count) { $aParts[$i] } else { 0 })
        $bv = $(if ($i -lt $bParts.Count) { $bParts[$i] } else { 0 })
        if ($av -lt $bv) { return $true }
        if ($av -gt $bv) { return $false }
    }
    return $false
}

if (VersionLessThan $verOut $PythonMinVersion) {
    Fail "Python $PythonMinVersion or newer is required. Found $verOut."
}

Write-Info "Using Python: $pythonCmd ($verOut)"

# 4. Create venv
$venvPath = Join-Path $ScriptDir ".venv"
if (-not (Test-Path $venvPath)) {
    Write-Info "Creating virtual environment in .venv ..."
    & $pythonCmd -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "Failed to create virtual environment." }
} else {
    Write-Info "Virtual environment already exists."
}

# 5. Activate venv and install requirements
$venvPython = Join-Path $venvPath "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Fail "Could not find venv python at $venvPython"
}

$reqFile = Join-Path $ScriptDir "requirements.txt"
if (-not (Test-Path $reqFile)) {
    Fail "requirements.txt not found in $ScriptDir"
}

Write-Info "Installing Python dependencies ..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r $reqFile
if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }

# 6. Generate config.ini from template if needed
$configPath = Join-Path $ScriptDir "config.ini"
$templatePath = Join-Path $ScriptDir "config-template.ini"
if (-not (Test-Path $configPath)) {
    if (Test-Path $templatePath) {
        Copy-Item $templatePath $configPath
        Write-Info "Created config.ini from template."
    } else {
        # create a minimal default
        @"
[dolphin]
# point this to your Dolphin.exe or leave blank and set in GUI
path=

[hud]
# screen size etc.
width=1280
height=720
"@ | Out-File -Encoding UTF8 $configPath
        Write-Info "Created minimal config.ini."
    }
} else {
    Write-Info "config.ini already exists, leaving it."
}

Write-Info ""
Write-Info "Setup completed."
Write-Info "To run: .\.venv\Scripts\python.exe main.py"
