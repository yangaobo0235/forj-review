[CmdletBinding()]
param(
    [switch]$Local
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$extensionDirectory = Join-Path $projectRoot "apps/extension"
$distDirectory = Join-Path $extensionDirectory "dist"
$outputDirectory = Join-Path $projectRoot ".tmp"
$outputName = if ($Local) { "forj-review-extension-local.zip" } else { "forj-review-extension-public.zip" }
$outputZip = Join-Path $outputDirectory $outputName
$buildScript = if ($Local) { "build" } else { "build:public" }

if (-not (Test-Path -LiteralPath $extensionDirectory -PathType Container)) {
    throw "Frontend directory not found: $extensionDirectory"
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm was not found. Install Node.js first."
}

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
Remove-Item -LiteralPath $outputZip -Force -ErrorAction SilentlyContinue

Write-Host "Building frontend ($buildScript)..." -ForegroundColor Cyan
& npm --prefix $extensionDirectory run $buildScript
if ($LASTEXITCODE -ne 0) {
    throw "Frontend build failed."
}

if (-not (Test-Path -LiteralPath $distDirectory -PathType Container)) {
    throw "Frontend dist directory was not generated: $distDirectory"
}

Write-Host "Creating frontend ZIP..." -ForegroundColor Cyan
Compress-Archive -Path (Join-Path $distDirectory "*") -DestinationPath $outputZip -Force

$result = Get-Item -LiteralPath $outputZip
$sizeMb = [Math]::Round($result.Length / 1MB, 2)

Write-Host ""
Write-Host "Frontend package completed." -ForegroundColor Green
Write-Host "ZIP: $outputZip"
Write-Host "Size: $sizeMb MB"
if ($Local) {
    Write-Host "Mode: local (127.0.0.1:8010)"
} else {
    Write-Host "Mode: public (Tencent Cloud server)"
}
