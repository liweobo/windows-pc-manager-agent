$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$versionSource = Join-Path $projectRoot "src\pc_manager_agent\version.py"
$versionLine = Select-String -LiteralPath $versionSource -Pattern '^__version__ = "(?<version>\d+\.\d+\.\d+(?:-rc\.\d+)?)"$'
if (-not $versionLine) { throw "VERSION_SOURCE_INVALID" }
$version = $versionLine.Matches[0].Groups['version'].Value
$generated = Join-Path $projectRoot "build\generated"
$specs = @(
    "packaging\pc-manager-agent.spec",
    "packaging\pc-manager-privileged-broker.spec",
    "packaging\pc-manager-browser-worker.spec"
)

Push-Location $projectRoot
try {
    uv sync --all-groups --locked
    if ($LASTEXITCODE -ne 0) { throw "LOCKED_DEPENDENCY_SYNC_FAILED:$LASTEXITCODE" }
    uv run python scripts/generate_version_info.py $versionSource $generated
    if ($LASTEXITCODE -ne 0) { throw "VERSION_RESOURCE_GENERATION_FAILED:$LASTEXITCODE" }
    foreach ($spec in $specs) {
        uv run pyinstaller --clean --noconfirm $spec
        if ($LASTEXITCODE -ne 0) { throw "PYINSTALLER_BUILD_FAILED:${spec}:$LASTEXITCODE" }
    }

    $manifestCopies = @{
        "dist\pc-manager-agent\pc-manager-agent.exe.manifest" = "packaging\manifests\pc-manager-agent.exe.manifest"
        "dist\pc-manager-privileged-broker\pc-manager-privileged-broker.exe.manifest" = "packaging\manifests\pc-manager-privileged-broker.exe.manifest"
        "dist\pc-manager-browser-worker\pc-manager-browser-worker.exe.manifest" = "packaging\manifests\pc-manager-browser-worker.exe.manifest"
    }
    foreach ($destination in $manifestCopies.Keys) {
        Copy-Item -LiteralPath $manifestCopies[$destination] -Destination $destination -Force
    }

    uv run python scripts/inspect_release_artifacts.py `
        --main dist/pc-manager-agent `
        --broker dist/pc-manager-privileged-broker `
        --browser-worker dist/pc-manager-browser-worker `
        --broker-xref build/pc-manager-privileged-broker/xref-pc-manager-privileged-broker.html
    if ($LASTEXITCODE -ne 0) { throw "ARTIFACT_INSPECTION_FAILED:$LASTEXITCODE" }

    $executables = @(
        "dist\pc-manager-agent\pc-manager-agent.exe",
        "dist\pc-manager-privileged-broker\pc-manager-privileged-broker.exe",
        "dist\pc-manager-browser-worker\pc-manager-browser-worker.exe"
    )
    foreach ($executable in $executables) {
        $item = Get-Item -LiteralPath $executable
        $digest = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($item.VersionInfo.ProductVersion -ne $version) {
            throw "ARTIFACT_VERSION_MISMATCH:$executable"
        }
        Write-Output "$executable SHA256=$digest VERSION=$($item.VersionInfo.ProductVersion)"
    }
    Write-Output "SIGNING_NOT_PRODUCTION_READY: binaries are intentionally unsigned until a real certificate is configured."
}
finally {
    Pop-Location
}
