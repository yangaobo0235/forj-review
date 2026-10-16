[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "release-common.ps1")
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$tempRoot = Join-Path $projectRoot ".tmp"
$outputDirectory = Join-Path $tempRoot "server-release"
$toolsDirectory = Join-Path $tempRoot "server-deploy"
$packageName = "forj-review-server.tar.gz"
$imageName = "forj-review-api:main"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found. Install and start Docker Desktop first."
}
& docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running. Wait for Engine running." }
if (-not (Get-Command tar -ErrorAction SilentlyContinue)) { throw "tar was not found. Windows 10/11 includes tar.exe." }

New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
$stage = Join-Path $tempRoot ("server-stage-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    $revision = (& git -C $projectRoot rev-parse --short=12 HEAD)
    if ($LASTEXITCODE -ne 0) { throw "Cannot read the source revision." }
    $dirty = [bool](& git -C $projectRoot status --porcelain)
    $releaseId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-" + $revision
    if ($dirty) { $releaseId += "-dirty" }
    Write-Host "Building $imageName ($releaseId)..." -ForegroundColor Cyan
    & docker build --platform linux/amd64 --build-arg "RELEASE_ID=$releaseId" --label "org.opencontainers.image.revision=$revision" --label "org.opencontainers.image.version=$releaseId" --file (Join-Path $projectRoot "deploy/Dockerfile") --tag $imageName $projectRoot
    if ($LASTEXITCODE -ne 0) { throw "Docker image build failed; previous release files are retained." }
    $imageId = (& docker image inspect --format '{{.Id}}' $imageName)
    if ($LASTEXITCODE -ne 0) { throw "Cannot inspect the built image." }
    & docker save --output (Join-Path $stage "forj-review-api-main.tar") $imageName
    if ($LASTEXITCODE -ne 0) { throw "Docker export failed; previous release files are retained." }
    $toolsStage = Join-Path $stage "deploy-tools"
    New-Item -ItemType Directory -Path $toolsStage | Out-Null
    # Bash must receive LF without BOM, even from a Windows checkout.
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    foreach ($name in @("start-review.sh", "stop-review.sh", "rollback-review.sh", "review-common.sh")) {
        $source = [IO.File]::ReadAllText((Join-Path $projectRoot "deploy/$name"))
        [IO.File]::WriteAllText((Join-Path $toolsStage $name), $source.Replace("`r`n", "`n"), $utf8)
    }
    Copy-Item -LiteralPath (Join-Path $projectRoot "docker-compose.server.yml") -Destination $toolsStage
    Copy-Item -LiteralPath (Join-Path $projectRoot "deploy/recover-legacy-compose.py") -Destination $toolsStage
    Copy-Item -LiteralPath (Join-Path $projectRoot "deploy/DEPLOY.md") -Destination $toolsStage
    $metadata = [ordered]@{
        release_id = $releaseId; source_revision = $revision; working_tree_dirty = $dirty
        image = $imageName; image_id = $imageId; platform = "linux/amd64"
        created_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    }
    [IO.File]::WriteAllText((Join-Path $stage "release.json"), ($metadata | ConvertTo-Json) + "`n", $utf8)
    $checksums = foreach ($name in @("forj-review-api-main.tar", "release.json")) {
        (Get-ReleaseSha256 (Join-Path $stage $name)) + "  " + $name
    }
    [IO.File]::WriteAllText((Join-Path $stage "SHA256SUMS"), ($checksums -join "`n") + "`n", $utf8)
    Write-Host "Creating single backend package..." -ForegroundColor Cyan
    & tar -czf (Join-Path $stage $packageName) -C $stage forj-review-api-main.tar release.json SHA256SUMS
    if ($LASTEXITCODE -ne 0) { throw "Release archive creation failed; previous package retained." }
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    New-Item -ItemType Directory -Force -Path $toolsDirectory | Out-Null
    foreach ($file in (Get-ChildItem -LiteralPath $toolsStage -File)) {
        Move-Item -LiteralPath $file.FullName -Destination (Join-Path $toolsDirectory $file.Name) -Force
    }
    Move-Item -LiteralPath (Join-Path $stage $packageName) -Destination (Join-Path $outputDirectory $packageName) -Force
    Write-Host "Backend package: $(Join-Path $outputDirectory $packageName)" -ForegroundColor Green
    Write-Host "Server scripts/Compose (upload once, then only when changed): $toolsDirectory"
    Write-Host "Routine update: stop-review.sh -> manually upload ONLY $packageName -> start-review.sh"
    Write-Host "Upload destination: /home/ubuntu/review-agent-deploy/ (no manual extraction needed)."
} finally {
    # Only remove this invocation's staging folder inside the workspace temp root.
    $resolvedStage = [IO.Path]::GetFullPath($stage)
    $allowedPrefix = [IO.Path]::GetFullPath($tempRoot) + [IO.Path]::DirectorySeparatorChar + "server-stage-"
    if (-not $resolvedStage.StartsWith($allowedPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Invalid staging path." }
    Remove-Item -LiteralPath $resolvedStage -Recurse -Force
}
