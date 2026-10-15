[CmdletBinding()]
param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8010,
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python. Run uv sync in the repository root first."
}

Push-Location $projectRoot
try {
    $arguments = @("forj_review_assistant.main:app", "--host", $BindHost, "--port", $Port)
    if (-not $NoReload) {
        $arguments += "--reload"
    }

    Write-Host "Starting review Agent at http://${BindHost}:${Port}" -ForegroundColor Cyan
    # Some Windows application-control policies block the uvicorn.exe shim while
    # allowing the virtual-environment Python interpreter to run module entrypoints.
    & $python -m uvicorn @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Agent exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
