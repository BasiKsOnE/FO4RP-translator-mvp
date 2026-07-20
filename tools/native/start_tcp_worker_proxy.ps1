[CmdletBinding()]
param(
    [string] $ProxyUrl = $(if ($env:FORP_PROXY_URL) { $env:FORP_PROXY_URL } else { "http://127.0.0.1:8787/translate" }),
    [string] $Token = $(if ($env:FORP_PROXY_TOKEN) { $env:FORP_PROXY_TOKEN } else { "dev-token" }),
    [string] $TimeoutSeconds = $(if ($env:FORP_PROXY_TIMEOUT_SECONDS) { $env:FORP_PROXY_TIMEOUT_SECONDS } else { "4" }),
    [string] $Workers = $(if ($env:FORP_TCP_WORKERS) { $env:FORP_TCP_WORKERS } else { "12" }),
    [string] $MaxPending = $(if ($env:FORP_TCP_MAX_PENDING) { $env:FORP_TCP_MAX_PENDING } else { "0" }),
    [string] $Backlog = $(if ($env:FORP_TCP_BACKLOG) { $env:FORP_TCP_BACKLOG } else { "64" })
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")

$env:FORP_TRANSLATION_BACKEND = "proxy"
$env:FORP_PROXY_URL = $ProxyUrl
$env:FORP_PROXY_TOKEN = $Token
$env:FORP_PROXY_TIMEOUT_SECONDS = $TimeoutSeconds
$env:FORP_TCP_WORKERS = $Workers
$env:FORP_TCP_MAX_PENDING = $MaxPending
$env:FORP_TCP_BACKLOG = $Backlog

$exitCode = 0
Push-Location $repoRoot
try {
    & py -3.13 tools\native\tcp_echo_worker.py
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $exitCode
