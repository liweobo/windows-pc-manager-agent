param(
    [Parameter(Mandatory = $true)]
    [string]$InstallDirectory,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedVersion,
    [switch]$RequireValidSignature
)

$ErrorActionPreference = "Stop"
$resolved = (Resolve-Path -LiteralPath $InstallDirectory).Path
$programFilesRoots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) |
    Where-Object { $_ } |
    ForEach-Object { (Resolve-Path -LiteralPath $_).Path.TrimEnd('\') + '\' }
if (-not ($programFilesRoots | Where-Object { $resolved.StartsWith($_, [StringComparison]::OrdinalIgnoreCase) })) {
    throw "INSTALL_LOCATION_UNTRUSTED"
}

$main = Join-Path $resolved "pc-manager-agent.exe"
$broker = Join-Path $resolved "broker\pc-manager-privileged-broker.exe"
foreach ($binary in @($main, $broker)) {
    if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) { throw "INSTALLED_BINARY_MISSING:$binary" }
    $item = Get-Item -LiteralPath $binary
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "INSTALLED_BINARY_REPARSE:$binary" }
    if ($item.VersionInfo.ProductVersion -ne $ExpectedVersion) { throw "INSTALLED_VERSION_MISMATCH:$binary" }
    if ($RequireValidSignature) {
        $signature = Get-AuthenticodeSignature -LiteralPath $binary
        if ($signature.Status -ne 'Valid') { throw "INSTALLED_SIGNATURE_INVALID:$binary" }
    }
}

$acl = Get-Acl -LiteralPath $resolved
$accessRules = $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])
$currentUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$untrustedWriteSids = @('S-1-1-0', 'S-1-5-11', 'S-1-5-32-545', $currentUserSid)
$ordinaryWrite = $accessRules | Where-Object {
    $_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
    $_.IdentityReference.Value -in $untrustedWriteSids -and
    ($_.FileSystemRights -band (
        [Security.AccessControl.FileSystemRights]::Write -bor
        [Security.AccessControl.FileSystemRights]::Modify -bor
        [Security.AccessControl.FileSystemRights]::FullControl
    ))
}
if ($ordinaryWrite) { throw "INSTALL_ACL_ORDINARY_USER_WRITABLE" }
Write-Output "INSTALLED_LAYOUT_VERIFIED:$resolved"
