[CmdletBinding()]
param(
    [string] $Backend = $(if ($env:FORP_PROXY_BACKEND) { $env:FORP_PROXY_BACKEND } else { "google" }),
    [string] $HostName = $(if ($env:FORP_PROXY_HOST) { $env:FORP_PROXY_HOST } else { "127.0.0.1" }),
    [string] $Port = $(if ($env:FORP_PROXY_PORT) { $env:FORP_PROXY_PORT } else { "8787" }),
    [string] $Token = $(if ($env:FORP_PROXY_TOKEN) { $env:FORP_PROXY_TOKEN } else { "dev-token" }),
    [string] $CredentialPath
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")

$env:FORP_PROXY_BACKEND = $Backend
$env:FORP_PROXY_HOST = $HostName
$env:FORP_PROXY_PORT = $Port
$env:FORP_PROXY_TOKEN = $Token

if ($PSBoundParameters.ContainsKey("CredentialPath") -and $CredentialPath) {
    $resolvedCredentialPath = Resolve-Path -LiteralPath $CredentialPath
    $env:GOOGLE_APPLICATION_CREDENTIALS = $resolvedCredentialPath.Path
}

$exitCode = 0
Push-Location $repoRoot
try {
    & py -3.13 tools\native\translation_proxy.py
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $exitCode
