# AssetBridge setup for Windows. Run from this folder:
#   powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Write-Host "Python isn't installed. Install Python 3.11+ from python.org (tick 'Add python.exe to PATH'), then run this again." -ForegroundColor Red
    exit 1
}
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { Write-Host "Python 3.11 or newer is required." -ForegroundColor Red; exit 1 }

Write-Host "`n== Python packages" -ForegroundColor Cyan
py -3 -m pip install --user --upgrade -r requirements.txt
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "`n== Splat converter" -ForegroundColor Cyan
if (Get-Command npm -ErrorAction SilentlyContinue) {
    npm install --prefix tools --no-fund --no-audit "@playcanvas/splat-transform@3"
} else {
    Write-Host "Node.js not found: splat PLY<->SPZ conversion stays off until you install Node.js LTS and re-run setup." -ForegroundColor Yellow
}

if (-not (Test-Path config\libraries.json)) { Copy-Item config\libraries.example.json config\libraries.json }

Write-Host "`n== Branding, icons, plugins, desktop app" -ForegroundColor Cyan
py -3 tools\apply_brand.py
$ok = $LASTEXITCODE

$brand = Get-Content brand.json -Raw | ConvertFrom-Json
if ($brand.c4d.command_id -le 1000010 -or $brand.c4d.receiver_id -le 1000010) {
    Write-Host ""
    Write-Host "!! Cinema 4D plugin IDs are still Maxon's TESTING IDs (1000007 / 1000008)." -ForegroundColor Yellow
    Write-Host "!! Register two free IDs at https://developers.maxon.net (Plugin IDs), put them in brand.json" -ForegroundColor Yellow
    Write-Host "!! as c4d.command_id and c4d.receiver_id, then run: py -3 tools\apply_brand.py --no-app" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Next: add your library folders to config\libraries.json (see config\libraries.example.json)," -ForegroundColor Green
Write-Host "then open $($brand.name) from the desktop shortcut. Restart Cinema 4D and Blender to load the plugins." -ForegroundColor Green
exit $ok
