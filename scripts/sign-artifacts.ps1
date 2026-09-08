param(
    [Parameter(Mandatory = $true)]
    [string[]]$Paths,
    [string]$CertificateThumbprint = $env:PC_MANAGER_SIGNING_CERT_THUMBPRINT,
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
if (-not $CertificateThumbprint) {
    throw "SIGNING_NOT_CONFIGURED: configure a certificate in Windows Certificate Store; never pass private key material"
}
$signTool = Get-ChildItem -Path "${env:ProgramFiles(x86)}\Windows Kits\10\bin" `
    -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
    Sort-Object FullName -Descending |
    Select-Object -First 1
if (-not $signTool) { throw "SIGNTOOL_NOT_FOUND" }

foreach ($path in $Paths) {
    $resolved = (Resolve-Path -LiteralPath $path).Path
    & $signTool.FullName sign /sha1 $CertificateThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $resolved
    if ($LASTEXITCODE -ne 0) { throw "SIGNING_FAILED:$resolved" }
    & $signTool.FullName verify /pa /all /v $resolved
    if ($LASTEXITCODE -ne 0) { throw "SIGNATURE_VERIFICATION_FAILED:$resolved" }
    $signature = Get-AuthenticodeSignature -LiteralPath $resolved
    if ($signature.Status -ne 'Valid') { throw "AUTHENTICODE_NOT_VALID:$resolved" }
}
