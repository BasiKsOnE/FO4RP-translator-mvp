[CmdletBinding()]
param(
    [string]$RepoRoot = "C:\FOnlines\FO4RP",
    [string]$ServerRoot = "C:\FOnlines\FO4RP_DLL_SMOKE_SERVER",
    [string]$ClientRoot = "C:\FOnlines\TLJ_CLIENT_LOCAL",
    [string]$ExpectedMainDllHash = "95C244FEBDD6860966314108551096D70D664FC6177253E35AA1D28014452742"
)

. (Join-Path $PSScriptRoot "native-common.ps1")

Assert-NativeExactPath -Path $RepoRoot -Expected "C:\FOnlines\FO4RP" -Description "Repository" | Out-Null
Assert-NativeExactPath -Path $ServerRoot -Expected "C:\FOnlines\FO4RP_DLL_SMOKE_SERVER" -Description "Server" | Out-Null
Assert-NativeExactPath -Path $ClientRoot -Expected "C:\FOnlines\TLJ_CLIENT_LOCAL" -Description "Client" | Out-Null

$manifestPath = Join-Path $ServerRoot "_native_echo_state\last_deploy.json"
Assert-NativePath -Path $manifestPath -Description "Deployment manifest"
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json

Assert-NativeExactPath -Path ([string]$manifest.RepoRoot) -Expected $RepoRoot -Description "Manifest repository" | Out-Null
Assert-NativeExactPath -Path ([string]$manifest.ServerRoot) -Expected $ServerRoot -Description "Manifest server" | Out-Null
Assert-NativeExactPath -Path ([string]$manifest.ClientRoot) -Expected $ClientRoot -Description "Manifest client" | Out-Null
if ([int]$manifest.Port -ne 4001) {
    throw "Invalid rollback manifest port."
}
if ([string]$manifest.Status -eq "RolledBack") {
    throw "Deployment is already rolled back."
}

$backupRoot = Join-Path $ServerRoot "_native_echo_backups"
$backupDir = Assert-NativeContainedPath -Path ([string]$manifest.BackupDir) -Parent $backupRoot -Description "Backup directory"
Assert-NativePath -Path $backupDir -Description "Backup directory"

$serverDll = Join-Path $ServerRoot "scripts\rust_dll\client.dll"
$serverBindings = Join-Path $ServerRoot "scripts\rust_bindings.fos"
$serverClientMain = Join-Path $ServerRoot "scripts\client_main.fos"
$cacheFile = Join-Path $ClientRoot "data\cache\localhost.4001.cache"
$runtimeDir = Join-Path $ClientRoot "data\cache\localhost.4001"
$serverExe = Join-Path $ServerRoot "FOnlineServer.exe"
$clientExe = Join-Path $ClientRoot "FOnline.exe"

$serverDllBackup = Join-Path $backupDir "server\scripts\rust_dll\client.dll"
$serverBindingsBackup = Join-Path $backupDir "server\scripts\rust_bindings.fos"
$serverClientMainBackup = Join-Path $backupDir "server\scripts\client_main.fos"
$cacheFileBackup = Join-Path $backupDir "client_cache\localhost.4001.cache"

$hashes = $manifest.OriginalHashes

# Preflight every recovery file before overwriting anything.
Assert-NativeSha256 -Path $serverDllBackup -Expected ([string]$hashes.ServerDll) | Out-Null
Assert-NativeSha256 -Path $serverBindingsBackup -Expected ([string]$hashes.ServerBindings) | Out-Null
Assert-NativeSha256 -Path $serverClientMainBackup -Expected ([string]$hashes.ServerClientMain) | Out-Null
Assert-NativeSha256 -Path $cacheFileBackup -Expected ([string]$hashes.CacheFile) | Out-Null

Write-Host "Stopping only the exact isolated test executables..."
Stop-NativeIsolatedProcesses -ExecutablePaths @($serverExe, $clientExe) | Out-Null
Start-Sleep -Milliseconds 500

try {
    Write-Host "Restoring copied-server files..."
    Copy-NativeFileVerified -Source $serverDllBackup -Destination $serverDll | Out-Null
    Copy-NativeFileVerified -Source $serverBindingsBackup -Destination $serverBindings | Out-Null
    Copy-NativeFileVerified -Source $serverClientMainBackup -Destination $serverClientMain | Out-Null

    Write-Host "Restoring seeded port-4001 cache..."
    Copy-NativeFileVerified -Source $cacheFileBackup -Destination $cacheFile | Out-Null

    if (Test-Path -LiteralPath $runtimeDir) {
        Remove-Item -LiteralPath $runtimeDir -Recurse -Force
    }

    Assert-NativeSha256 -Path $serverDll -Expected ([string]$hashes.ServerDll) | Out-Null
    Assert-NativeSha256 -Path $serverBindings -Expected ([string]$hashes.ServerBindings) | Out-Null
    Assert-NativeSha256 -Path $serverClientMain -Expected ([string]$hashes.ServerClientMain) | Out-Null
    Assert-NativeSha256 -Path $cacheFile -Expected ([string]$hashes.CacheFile) | Out-Null

    $mainRepoDll = Join-Path $RepoRoot "scripts\rust_dll\client.dll"
    Assert-NativeSha256 -Path $mainRepoDll -Expected $ExpectedMainDllHash | Out-Null

    $changes = @(Assert-NativeRepoChanges `
        -RepoRoot $RepoRoot `
        -AllowedPaths @("scripts\client_main.fos", "scripts\rust_bindings.fos"))

    $rolledBackAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    $manifest.Status = "RolledBack"
    $manifest.UpdatedAtUtc = $rolledBackAtUtc
    $manifest |
        Add-Member `
            -NotePropertyName "RolledBackAtUtc" `
            -NotePropertyValue $rolledBackAtUtc `
            -Force
    $manifest.LastError = $null
    Write-NativeJson -InputObject $manifest -Path $manifestPath

    $rollbackRecord = [pscustomobject]@{
        RolledBackAtUtc = $rolledBackAtUtc
        Manifest = $manifestPath
        BackupDir = $backupDir
        RestoredServerDllHash = Get-NativeSha256 -Path $serverDll
        RestoredBindingsHash = Get-NativeSha256 -Path $serverBindings
        RestoredClientMainHash = Get-NativeSha256 -Path $serverClientMain
        RestoredCacheHash = Get-NativeSha256 -Path $cacheFile
        MainRepoDllHash = Get-NativeSha256 -Path $mainRepoDll
        AllowedRepoChanges = @($changes | ForEach-Object { $_.Raw })
        RuntimeDirectoryRemoved = -not (Test-Path -LiteralPath $runtimeDir)
    }

    $recordPath = Join-Path $ServerRoot ("_native_echo_state\rollback_" + (Get-NativeTimestamp) + ".json")
    Write-NativeJson -InputObject $rollbackRecord -Path $recordPath
}
catch {
    $originalError = $_
    $manifest.Status = "RollbackFailed"
    $manifest.UpdatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    $manifest.LastError = $originalError.Exception.Message
    try {
        Write-NativeJson -InputObject $manifest -Path $manifestPath
    }
    catch {
        Write-Warning "Unable to update failed rollback manifest: $($_.Exception.Message)"
    }
    throw $originalError
}

Write-Host ""
Write-Host "ROLLBACK PASS"
Write-Host "Restored from: $backupDir"
Write-Host "The extracted localhost.4001 directory was intentionally left absent."
