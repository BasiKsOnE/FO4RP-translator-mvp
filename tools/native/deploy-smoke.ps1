[CmdletBinding()]
param(
    [string]$RepoRoot = "C:\FOnlines\FO4RP",
    [string]$ServerRoot = "C:\FOnlines\FO4RP_DLL_SMOKE_SERVER",
    [string]$ClientRoot = "C:\FOnlines\TLJ_CLIENT_LOCAL",
    [string]$CandidatePath = "C:\FOnlines\RUST_DLL_BUILD_PROBE\20260705_013208\target_native_echo\i686-pc-windows-msvc\release\tnf_client_dll.dll",
    [string]$ExpectedCandidateHash = "6CDC3B704852DBEAE0F0625EB36BC02C612437D09CD207B0C5A2403BBA03FD98",
    [string]$ExpectedMainDllHash = "95C244FEBDD6860966314108551096D70D664FC6177253E35AA1D28014452742",
    [int]$Port = 4001,
    [switch]$NoLaunch
)

. (Join-Path $PSScriptRoot "native-common.ps1")

$expectedRepoRoot = "C:\FOnlines\FO4RP"
$expectedServerRoot = "C:\FOnlines\FO4RP_DLL_SMOKE_SERVER"
$expectedClientRoot = "C:\FOnlines\TLJ_CLIENT_LOCAL"
$expectedCandidatePath = "C:\FOnlines\RUST_DLL_BUILD_PROBE\20260705_013208\target_native_echo\i686-pc-windows-msvc\release\tnf_client_dll.dll"

Assert-NativeExactPath -Path $RepoRoot -Expected $expectedRepoRoot -Description "Repository" | Out-Null
Assert-NativeExactPath -Path $ServerRoot -Expected $expectedServerRoot -Description "Server" | Out-Null
Assert-NativeExactPath -Path $ClientRoot -Expected $expectedClientRoot -Description "Client" | Out-Null
Assert-NativeExactPath -Path $CandidatePath -Expected $expectedCandidatePath -Description "Candidate DLL" | Out-Null
if ($Port -ne 4001) {
    throw "Native smoke deployment is restricted to port 4001."
}

$repoDll = Join-Path $RepoRoot "scripts\rust_dll\client.dll"
$repoBindings = Join-Path $RepoRoot "scripts\rust_bindings.fos"
$repoClientMain = Join-Path $RepoRoot "scripts\client_main.fos"

$serverDll = Join-Path $ServerRoot "scripts\rust_dll\client.dll"
$serverBindings = Join-Path $ServerRoot "scripts\rust_bindings.fos"
$serverClientMain = Join-Path $ServerRoot "scripts\client_main.fos"
$serverExe = Join-Path $ServerRoot "FOnlineServer.exe"
$serverConfig = Join-Path $ServerRoot "FOnlineServer.cfg"

$clientExe = Join-Path $ClientRoot "FOnline.exe"
$cacheRoot = Join-Path $ClientRoot "data\cache"
$cacheFile = Join-Path $cacheRoot "localhost.4001.cache"
$runtimeDir = Join-Path $cacheRoot "localhost.4001"

$stateRoot = Join-Path $ServerRoot "_native_echo_state"
$backupRoot = Join-Path $ServerRoot "_native_echo_backups"
$timestamp = Get-NativeTimestamp
$backupDir = Join-Path $backupRoot $timestamp
$manifestPath = Join-Path $stateRoot "last_deploy.json"

Assert-NativePath -Path $RepoRoot -Description "FO4RP repository"
Assert-NativePath -Path $ServerRoot -Description "Isolated server"
Assert-NativePath -Path $ClientRoot -Description "Isolated client"
Assert-NativePath -Path $CandidatePath -Description "Candidate DLL"
Assert-NativePath -Path $cacheFile -Description "Seeded localhost.4001 cache"
Assert-NativePath -Path $serverConfig -Description "Copied server configuration"
Assert-NativePath -Path $serverExe -Description "Copied server executable"
Assert-NativePath -Path $clientExe -Description "Isolated client executable"

if (Test-Path -LiteralPath $manifestPath) {
    $previous = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ([string]$previous.Status -ne "RolledBack") {
        throw "An active deployment already exists with status '$($previous.Status)'. Run rollback-smoke.ps1 first."
    }
}

if (Test-Path -LiteralPath $backupDir) {
    throw "Backup directory already exists: $backupDir"
}

$changes = @(Assert-NativeRepoChanges `
    -RepoRoot $RepoRoot `
    -AllowedPaths @("scripts\client_main.fos", "scripts\rust_bindings.fos"))

Assert-NativeSha256 -Path $repoDll -Expected $ExpectedMainDllHash | Out-Null
$candidateHash = Assert-NativeSha256 -Path $CandidatePath -Expected $ExpectedCandidateHash

$configText = Get-Content -LiteralPath $serverConfig -Raw
if ($configText -notmatch '(?m)^\s*Port\s*=\s*4001\s*$') {
    throw "Copied server is not configured for port 4001: $serverConfig"
}

Write-Host "Stopping only the exact isolated test executables..."
$stopped = @(Stop-NativeIsolatedProcesses -ExecutablePaths @($serverExe, $clientExe))
Start-Sleep -Milliseconds 500

if (Test-NativeTcpPort -HostName "localhost" -Port $Port -TimeoutMs 500) {
    throw "Port ${Port} is still in use."
}

New-Item -ItemType Directory -Path $backupDir | Out-Null
New-Item -ItemType Directory -Force -Path $stateRoot | Out-Null

$backupServerRoot = Join-Path $backupDir "server"
$backupCacheRoot = Join-Path $backupDir "client_cache"
$serverDllBackup = Join-Path $backupServerRoot "scripts\rust_dll\client.dll"
$serverBindingsBackup = Join-Path $backupServerRoot "scripts\rust_bindings.fos"
$serverClientMainBackup = Join-Path $backupServerRoot "scripts\client_main.fos"
$cacheFileBackup = Join-Path $backupCacheRoot "localhost.4001.cache"

Write-Host "Creating verified backups..."
$originalServerDllHash = Copy-NativeFileVerified -Source $serverDll -Destination $serverDllBackup
$originalBindingsHash = Copy-NativeFileVerified -Source $serverBindings -Destination $serverBindingsBackup
$originalClientMainHash = Copy-NativeFileVerified -Source $serverClientMain -Destination $serverClientMainBackup
$originalCacheHash = Copy-NativeFileVerified -Source $cacheFile -Destination $cacheFileBackup
$logOffsets = @(Get-NativeLogSnapshot -ServerRoot $ServerRoot -ClientRoot $ClientRoot)

$head = (& git -C $RepoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read FO4RP HEAD."
}

$manifest = [ordered]@{
    StateVersion = 2
    Status = "BackedUp"
    CreatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    UpdatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    RepoRoot = $RepoRoot
    RepoHead = $head
    ServerRoot = $ServerRoot
    ClientRoot = $ClientRoot
    Port = $Port
    CandidatePath = $CandidatePath
    CandidateHash = $candidateHash
    BackupDir = $backupDir
    OriginalHashes = [ordered]@{
        ServerDll = $originalServerDllHash
        ServerBindings = $originalBindingsHash
        ServerClientMain = $originalClientMainHash
        CacheFile = $originalCacheHash
    }
    DeployedHashes = $null
    LogOffsets = $logOffsets
    StoppedProcessIds = @($stopped | ForEach-Object { $_.ProcessId })
    ServerPid = $null
    ClientPid = $null
    RolledBackAtUtc = $null
    LastError = $null
}

# Write the recovery manifest before changing any active file.
Write-NativeJson -InputObject $manifest -Path $manifestPath

try {
    Write-Host "Deploying approved DLL and AngelScript files to copied server..."
    $deployedDllHash = Copy-NativeFileVerified -Source $CandidatePath -Destination $serverDll
    $deployedBindingsHash = Copy-NativeFileVerified -Source $repoBindings -Destination $serverBindings
    $deployedClientMainHash = Copy-NativeFileVerified -Source $repoClientMain -Destination $serverClientMain

    if (Test-Path -LiteralPath $runtimeDir) {
        Write-Host "Removing only extracted localhost.4001 runtime directory..."
        Remove-Item -LiteralPath $runtimeDir -Recurse -Force
    }

    $manifest.Status = "Deployed"
    $manifest.UpdatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    $manifest.DeployedHashes = [ordered]@{
        ServerDll = $deployedDllHash
        ServerBindings = $deployedBindingsHash
        ServerClientMain = $deployedClientMainHash
    }
    Write-NativeJson -InputObject $manifest -Path $manifestPath

    if (-not $NoLaunch) {
        Write-Host "Starting copied server on port 4001..."
        $serverProcess = Start-Process `
            -FilePath $serverExe `
            -ArgumentList @("-start", "-") `
            -WorkingDirectory $ServerRoot `
            -PassThru

        $manifest.ServerPid = $serverProcess.Id
        $manifest.Status = "ServerStarted"
        $manifest.UpdatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
        Write-NativeJson -InputObject $manifest -Path $manifestPath

        Wait-NativeTcpPort -HostName "localhost" -Port $Port -TimeoutSeconds 120

        Write-Host "Starting isolated client..."
        $clientProcess = Start-Process `
            -FilePath $clientExe `
            -ArgumentList @("-RemoteHost", "localhost", "-RemotePort", "4001") `
            -WorkingDirectory $ClientRoot `
            -PassThru

        $manifest.ClientPid = $clientProcess.Id
        $manifest.Status = "Launched"
        $manifest.UpdatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
        Write-NativeJson -InputObject $manifest -Path $manifestPath
    }
}
catch {
    $originalError = $_
    $manifest.Status = "Failed"
    $manifest.UpdatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    $manifest.LastError = $originalError.Exception.Message
    try {
        Write-NativeJson -InputObject $manifest -Path $manifestPath
    }
    catch {
        Write-Warning "Unable to update failed deployment manifest: $($_.Exception.Message)"
    }
    throw $originalError
}

Write-Host ""
Write-Host "DEPLOY PASS"
Write-Host "Status:   $($manifest.Status)"
Write-Host "Backup:   $backupDir"
Write-Host "Manifest: $manifestPath"
Write-Host "DLL hash: $candidateHash"
if (-not $NoLaunch) {
    Write-Host "Server PID: $($manifest.ServerPid)"
    Write-Host "Client PID: $($manifest.ClientPid)"
    Write-Host "Next: .\tools\native\verify-smoke.ps1"
}
