[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$serviceDirectory = $projectRoot
$outputDirectory = Join-Path $projectRoot ".tmp"
$imageName = "forj-review-api:main"
$imageTar = Join-Path $outputDirectory "forj-review-api-main.tar"
$composeSource = Join-Path $projectRoot "docker-compose.server.yml"
$composeOutput = Join-Path $outputDirectory "docker-compose.server.yml"

if (-not (Test-Path -LiteralPath $serviceDirectory -PathType Container)) {
    throw "Backend directory not found: $serviceDirectory"
}

if (-not (Test-Path -LiteralPath $composeSource -PathType Leaf)) {
    throw "Docker Compose file not found: $composeSource"
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker command was not found. Install and start Docker Desktop first."
}

Write-Host "Checking Docker Desktop..." -ForegroundColor Cyan
& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is not running. Start it and wait for Engine running."
}

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
Remove-Item -LiteralPath $imageTar -Force -ErrorAction SilentlyContinue

Write-Host "Building backend Docker image: $imageName" -ForegroundColor Cyan
& docker build --platform linux/amd64 --file (Join-Path $projectRoot "deploy/Dockerfile") --tag $imageName $serviceDirectory
if ($LASTEXITCODE -ne 0) {
    throw "Docker image build failed."
}

Write-Host "Exporting backend image TAR..." -ForegroundColor Cyan
& docker save --output $imageTar $imageName
if ($LASTEXITCODE -ne 0) {
    throw "Docker image export failed."
}

Copy-Item -LiteralPath $composeSource -Destination $composeOutput -Force

$result = Get-Item -LiteralPath $imageTar
$sizeMb = [Math]::Round($result.Length / 1MB, 2)

Write-Host ""
Write-Host "Backend package completed." -ForegroundColor Green
Write-Host "TAR: $imageTar"
Write-Host "Size: $sizeMb MB"
Write-Host "Compose: $composeOutput"
Write-Host ""
Write-Host "Upload the TAR to:" -ForegroundColor Yellow
Write-Host "/home/ubuntu/review-agent-deploy/forj-review-api-main.tar"
