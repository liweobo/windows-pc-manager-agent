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

function Invoke-ProcessForExitCode(
    [string]$FilePath,
    [string[]]$Arguments = @(),
    [int]$TimeoutSeconds = 180
) {
    $startParameters = @{
        FilePath = $FilePath
        PassThru = $true
        WindowStyle = 'Hidden'
    }
    if ($Arguments.Count -gt 0) { $startParameters.ArgumentList = $Arguments }
    $process = Start-Process @startParameters
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw "PROCESS_TIMEOUT:${FilePath}:${TimeoutSeconds}"
    }
    return $process.ExitCode
}

function Invoke-CheckedProcess([string]$FilePath, [string[]]$Arguments) {
    $exitCode = Invoke-ProcessForExitCode -FilePath $FilePath -Arguments $Arguments
    if ($exitCode -ne 0) { throw "PROCESS_FAILED:${FilePath}:$exitCode" }
}

Invoke-CheckedProcess $installer @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
& (Join-Path $PSScriptRoot "test-installed-layout.ps1") -InstallDirectory $installDirectory -ExpectedVersion $expectedVersion
Invoke-CheckedProcess $installer @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
$main = Join-Path $installDirectory "pc-manager-agent.exe"
Invoke-CheckedProcess $main @('--version')
$runnerIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$runnerPrincipal = [Security.Principal.WindowsPrincipal]::new($runnerIdentity)
$runnerElevated = $runnerPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$smokeExitCode = Invoke-ProcessForExitCode -FilePath $main -Arguments @('--smoke-test') -TimeoutSeconds 120
if ($runnerElevated) {
    if ($smokeExitCode -ne 23) { throw "ELEVATED_MAIN_DENIAL_FAILED:$smokeExitCode" }
    Write-Output "ELEVATED_MAIN_DENIAL_VERIFIED"
} elseif ($smokeExitCode -ne 0) {
    throw "STANDARD_USER_MAIN_SMOKE_FAILED:$smokeExitCode"
}
$broker = Join-Path $installDirectory "broker\pc-manager-privileged-broker.exe"
$brokerExitCode = Invoke-ProcessForExitCode -FilePath $broker -TimeoutSeconds 120
if ($brokerExitCode -ne 20) { throw "BROKER_NO_AUTHORITY_GUARD_FAILED:$brokerExitCode" }
$uninstaller = Join-Path $installDirectory "unins000.exe"
Invoke-CheckedProcess $uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
if (Test-Path -LiteralPath $main) { throw "UNINSTALL_LEFT_MAIN_BINARY" }
if (-not (Test-Path -LiteralPath $sentinel)) { throw "UNINSTALL_REMOVED_USER_STATE" }
Write-Output "INSTALL_REINSTALL_UNINSTALL_PRESERVATION_VERIFIED"
