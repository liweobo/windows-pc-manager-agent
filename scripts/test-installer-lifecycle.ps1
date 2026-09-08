param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,
    [switch]$CiEphemeralHost
)

$ErrorActionPreference = "Stop"
if (-not $CiEphemeralHost -or $env:CI -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw "INSTALLER_LIFECYCLE_REQUIRES_EXPLICIT_EPHEMERAL_GITHUB_HOST"
}
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$versionSource = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path "src\pc_manager_agent\version.py"
$versionLine = Select-String -LiteralPath $versionSource -Pattern '^__version__ = "(?<version>\d+\.\d+\.\d+(?:-rc\.\d+)?)"$'
if (-not $versionLine) { throw "VERSION_SOURCE_INVALID" }
$expectedVersion = $versionLine.Matches[0].Groups['version'].Value
$installDirectory = Join-Path $env:ProgramFiles "Windows PC Manager Agent"
$sentinelDirectory = Join-Path $env:LOCALAPPDATA "WindowsPCManagerAgent"
$sentinel = Join-Path $sentinelDirectory "installer-preserve-sentinel.txt"
New-Item -ItemType Directory -Path $sentinelDirectory -Force | Out-Null
Set-Content -LiteralPath $sentinel -Value "synthetic-ci-state" -Encoding utf8NoBOM

function Invoke-CheckedProcess([string]$FilePath, [string[]]$Arguments) {
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -PassThru -Wait -WindowStyle Hidden
    if ($process.ExitCode -ne 0) { throw "PROCESS_FAILED:${FilePath}:$($process.ExitCode)" }
}

Invoke-CheckedProcess $installer @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
& (Join-Path $PSScriptRoot "test-installed-layout.ps1") -InstallDirectory $installDirectory -ExpectedVersion $expectedVersion
Invoke-CheckedProcess $installer @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
$main = Join-Path $installDirectory "pc-manager-agent.exe"
Invoke-CheckedProcess $main @('--version')
$broker = Join-Path $installDirectory "broker\pc-manager-privileged-broker.exe"
$brokerProcess = Start-Process -FilePath $broker -PassThru -Wait -WindowStyle Hidden
if ($brokerProcess.ExitCode -ne 20) { throw "BROKER_NO_AUTHORITY_GUARD_FAILED" }
$uninstaller = Join-Path $installDirectory "unins000.exe"
Invoke-CheckedProcess $uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
if (Test-Path -LiteralPath $main) { throw "UNINSTALL_LEFT_MAIN_BINARY" }
if (-not (Test-Path -LiteralPath $sentinel)) { throw "UNINSTALL_REMOVED_USER_STATE" }
Write-Output "INSTALL_REINSTALL_UNINSTALL_PRESERVATION_VERIFIED"
