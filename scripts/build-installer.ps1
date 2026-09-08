param(
    [string]$IsccPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$versionSource = Join-Path $projectRoot "src\pc_manager_agent\version.py"
$versionLine = Select-String -LiteralPath $versionSource -Pattern '^__version__ = "(?<version>(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)(?:-rc\.(?<rc>\d+))?)"$'
if (-not $versionLine) { throw "VERSION_SOURCE_INVALID" }
$match = $versionLine.Matches[0]
$version = $match.Groups['version'].Value
$rc = if ($match.Groups['rc'].Success) { $match.Groups['rc'].Value } else { '0' }
$numericVersion = "$($match.Groups['major'].Value).$($match.Groups['minor'].Value).$($match.Groups['patch'].Value).$rc"

if (-not $IsccPath) {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    )
    $IsccPath = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath)) {
    throw "INSTALLER_TOOL_NOT_FOUND: install Inno Setup 6 or pass -IsccPath"
}

$required = @(
    "dist\pc-manager-agent\pc-manager-agent.exe",
    "dist\pc-manager-privileged-broker\pc-manager-privileged-broker.exe"
)
foreach ($path in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $path))) {
        throw "INSTALLER_INPUT_MISSING:$path"
    }
}

Push-Location $projectRoot
try {
    & $IsccPath "/DMyAppVersion=$version" "/DMyAppNumericVersion=$numericVersion" "packaging\installer\windows-pc-manager-agent.iss"
    if ($LASTEXITCODE -ne 0) { throw "INSTALLER_BUILD_FAILED:$LASTEXITCODE" }
    Write-Output "Installer built for private RC. SIGNING_NOT_PRODUCTION_READY."
}
finally {
    Pop-Location
}
