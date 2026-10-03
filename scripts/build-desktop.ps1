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
Push-Location $Root
try {
    & $Python -m pip install -e "$Root\Desktop[build]"
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & $Python -m PyInstaller --noconfirm --clean --distpath "$Root\dist" --workpath "$Root\build\desktop" "$Root\Desktop\bluey.spec"
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    Compress-Archive -Path "$Root\dist\BlueyDesktop" -DestinationPath "$Root\dist\BlueyDesktop-windows-x64.zip" -Force
    Write-Host "Built $Root\dist\BlueyDesktop\BlueyDesktop.exe"
    Write-Host 'Keep the complete BlueyDesktop folder together when copying it.'
} finally {
    Pop-Location
}
