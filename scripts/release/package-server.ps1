[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "release-common.ps1")
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$tempRoot = Join-Path $projectRoot ".tmp"
$outputDirectory = Join-Path $tempRoot "server-release"
$imageName = "forj-review-api:main"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found. Install and start Docker Desktop first."
}
& docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running. Wait for Engine running." }

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
    Copy-Item -LiteralPath (Join-Path $projectRoot "docker-compose.server.yml") -Destination $stage
    # Bash must receive LF without BOM, even from a Windows checkout.
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    foreach ($name in @("start-review.sh", "stop-review.sh", "rollback-review.sh", "review-common.sh")) {
        $source = [IO.File]::ReadAllText((Join-Path $projectRoot "deploy/$name"))
        [IO.File]::WriteAllText((Join-Path $stage $name), $source.Replace("`r`n", "`n"), $utf8)
    }
    Copy-Item -LiteralPath (Join-Path $projectRoot "deploy/DEPLOY.md") -Destination $stage
    $metadata = [ordered]@{
        release_id = $releaseId; source_revision = $revision; working_tree_dirty = $dirty
        image = $imageName; image_id = $imageId; platform = "linux/amd64"
        created_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    }
    [IO.File]::WriteAllText((Join-Path $stage "release.json"), ($metadata | ConvertTo-Json) + "`n", $utf8)
    $checksums = foreach ($file in (Get-ChildItem -LiteralPath $stage -File | Sort-Object Name)) {
        (Get-ReleaseSha256 $file.FullName) + "  " + $file.Name
    }
    [IO.File]::WriteAllText((Join-Path $stage "SHA256SUMS"), ($checksums -join "`n") + "`n", $utf8)
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    foreach ($file in (Get-ChildItem -LiteralPath $stage -File | Where-Object Name -ne "SHA256SUMS")) {
        Move-Item -LiteralPath $file.FullName -Destination (Join-Path $outputDirectory $file.Name) -Force
    }
    Move-Item -LiteralPath (Join-Path $stage "SHA256SUMS") -Destination (Join-Path $outputDirectory "SHA256SUMS") -Force
    Write-Host "Release ready: $outputDirectory" -ForegroundColor Green
    Write-Host "Upload ALL files in this directory to /home/ubuntu/review-agent-deploy/"
    Write-Host "Then run: chmod +x *-review.sh && ./start-review.sh"
} finally {
    # Only remove this invocation's staging folder inside the workspace temp root.
    $resolvedStage = [IO.Path]::GetFullPath($stage)
    $allowedPrefix = [IO.Path]::GetFullPath($tempRoot) + [IO.Path]::DirectorySeparatorChar + "server-stage-"
    if (-not $resolvedStage.StartsWith($allowedPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Invalid staging path." }
    Remove-Item -LiteralPath $resolvedStage -Recurse -Force
}
