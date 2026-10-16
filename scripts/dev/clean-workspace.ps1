[CmdletBinding()]
param([switch]$Apply)

# Preview by default. Only confirmed, reproducible artifacts inside this checkout
# are eligible. Never remove application state, secrets, logs or installed runtimes.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$prefix = $workspace + [IO.Path]::DirectorySeparatorChar
$targets = @('.tmp', 'dist', '.pytest_cache', '.ruff_cache', '.tmp-agent-real-probe.py', '.tmp-extension-test.log') |
    ForEach-Object { Join-Path $workspace $_ }
foreach ($source in @('src', 'tests', 'scripts')) {
    $targets += @(Get-ChildItem -LiteralPath (Join-Path $workspace $source) -Recurse -Force -Directory -Filter '__pycache__' |
        ForEach-Object { $_.FullName })
}

# Validate and inventory the complete set before deleting anything.
$inventory = @(foreach ($target in ($targets | Sort-Object -Unique)) {
    $absolute = [IO.Path]::GetFullPath($target)
    if (-not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Cleanup target escaped workspace: $absolute"
    }
    if (-not (Test-Path -LiteralPath $absolute)) { continue }
    $item = Get-Item -LiteralPath $absolute -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Cleanup target is a link: $absolute"
    }
    if ($item.PSIsContainer) {
        $links = @(Get-ChildItem -LiteralPath $absolute -Recurse -Force -Attributes ReparsePoint)
        if ($links.Count) { throw "Cleanup target contains links; inspect it manually: $absolute" }
        $files = @(Get-ChildItem -LiteralPath $absolute -Recurse -Force -File)
    } else { $files = @($item) }
    [long]$bytes = 0
    foreach ($file in $files) { $bytes += $file.Length }
    [PSCustomObject]@{Path = $absolute; Files = $files.Count; Bytes = $bytes}
})

if ($Apply) {
    foreach ($entry in $inventory) {
        Remove-Item -LiteralPath $entry.Path -Recurse -Force
    }
}

[long]$totalBytes = 0
[long]$totalFiles = 0
foreach ($entry in $inventory) { $totalBytes += $entry.Bytes; $totalFiles += $entry.Files }
[PSCustomObject]@{
    Mode = $(if ($Apply) { 'Applied' } else { 'Preview (use -Apply to remove)' })
    TargetCount = $inventory.Count
    FileCount = $totalFiles
    MiB = [Math]::Round($totalBytes / 1MB, 2)
    Targets = $inventory
}
