$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$specPath = Join-Path $projectRoot "packaging\pc-manager-privileged-broker.spec"
$manifestPath = Join-Path $projectRoot "packaging\manifests\pc-manager-privileged-broker.exe.manifest"
$distribution = Join-Path $projectRoot "dist\pc-manager-privileged-broker"
$executable = Join-Path $distribution "pc-manager-privileged-broker.exe"
$adjacentManifest = "$executable.manifest"

Push-Location $projectRoot
try {
    uv run pyinstaller --clean --noconfirm $specPath
    Copy-Item -LiteralPath $manifestPath -Destination $adjacentManifest -Force
    $digest = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Output "Broker executable: $executable"
    Write-Output "Broker SHA-256: $digest"
    Write-Output "Development mode only; production mode additionally requires trusted signing."
}
finally {
    Pop-Location
}
