$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root '.venv'
$Python = Join-Path $Venv 'Scripts\python.exe'

if (-not (Test-Path $Python)) {
    if ($env:BLUEY_PYTHON) {
        & $env:BLUEY_PYTHON -m venv $Venv
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv $Venv
    } else {
        & python -m venv $Venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create a Python environment. Install Python 3.12 and try again.' }
}
& $Python -c "import sys; assert (3, 11) <= sys.version_info < (3, 15), 'Bluey requires Python 3.11 to 3.14; Python 3.12 is recommended.'"
if ($LASTEXITCODE -ne 0) { throw 'Unsupported Python version.' }
& $Python -c "import importlib.util, sys; sys.exit(importlib.util.find_spec('bluey') is None)"
if ($LASTEXITCODE -ne 0) {
    & $Python -m pip install -e (Join-Path $Root 'Desktop')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
Push-Location $Root
try {
    & $Python -m bluey @args
    $Result = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $Result
