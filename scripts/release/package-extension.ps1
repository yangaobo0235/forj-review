[CmdletBinding()]
param(
    [switch]$Local
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "release-common.ps1")

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

Write-Host "Installing locked frontend dependencies..." -ForegroundColor Cyan
& npm --prefix $extensionDirectory ci
if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed; previous ZIP retained." }

Write-Host "Building frontend ($buildScript)..." -ForegroundColor Cyan
& npm --prefix $extensionDirectory run $buildScript
if ($LASTEXITCODE -ne 0) {
    throw "Frontend build failed."
}

if (-not (Test-Path -LiteralPath $distDirectory -PathType Container)) {
    throw "Frontend dist directory was not generated: $distDirectory"
}

Write-Host "Creating frontend ZIP..." -ForegroundColor Cyan
$manifest = [IO.File]::ReadAllText((Join-Path $distDirectory "manifest.json"), [Text.Encoding]::UTF8) | ConvertFrom-Json
foreach ($entry in @("manifest.json", "index.html", "content.js", "background.js")) {
    if (-not (Test-Path -LiteralPath (Join-Path $distDirectory $entry) -PathType Leaf)) { throw "Missing extension entry: $entry" }
}
if ($manifest.manifest_version -ne 3) { throw "Unexpected extension manifest version." }
$stageZip = Join-Path $outputDirectory ("extension-stage-" + [Guid]::NewGuid().ToString("N") + ".zip")
try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory($distDirectory, $stageZip)
    Move-Item -LiteralPath $stageZip -Destination $outputZip -Force
} finally {
    if (Test-Path -LiteralPath $stageZip) { Remove-Item -LiteralPath $stageZip -Force }
}
$checksum = Get-ReleaseSha256 $outputZip
[IO.File]::WriteAllText(($outputZip + ".sha256"), "$checksum  $outputName`n", (New-Object System.Text.UTF8Encoding($false)))

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
