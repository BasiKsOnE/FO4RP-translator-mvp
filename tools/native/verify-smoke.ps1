[CmdletBinding()]
param(
    [string]$ServerRoot = "C:\FOnlines\FO4RP_DLL_SMOKE_SERVER",
    [string]$ClientRoot = "C:\FOnlines\TLJ_CLIENT_LOCAL",
    [string]$ExpectedCandidatePath = "C:\FOnlines\RUST_DLL_BUILD_PROBE\20260705_013208\target_native_echo\i686-pc-windows-msvc\release\tnf_client_dll.dll",
    [string]$ExpectedCandidateHash = "6CDC3B704852DBEAE0F0625EB36BC02C612437D09CD207B0C5A2403BBA03FD98",
    [int]$Port = 4001,
    [int]$WaitSeconds = 90
)

. (Join-Path $PSScriptRoot "native-common.ps1")

Assert-NativeExactPath -Path $ServerRoot -Expected "C:\FOnlines\FO4RP_DLL_SMOKE_SERVER" -Description "Server" | Out-Null
Assert-NativeExactPath -Path $ClientRoot -Expected "C:\FOnlines\TLJ_CLIENT_LOCAL" -Description "Client" | Out-Null
Assert-NativeExactPath -Path $ExpectedCandidatePath -Expected "C:\FOnlines\RUST_DLL_BUILD_PROBE\20260705_013208\target_native_echo\i686-pc-windows-msvc\release\tnf_client_dll.dll" -Description "Candidate DLL" | Out-Null
if ($Port -ne 4001) {
    throw "Verification is restricted to port 4001."
}

$manifestPath = Join-Path $ServerRoot "_native_echo_state\last_deploy.json"
Assert-NativePath -Path $manifestPath -Description "Deployment manifest"
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json

Assert-NativeExactPath -Path ([string]$manifest.ServerRoot) -Expected $ServerRoot -Description "Manifest server" | Out-Null
Assert-NativeExactPath -Path ([string]$manifest.ClientRoot) -Expected $ClientRoot -Description "Manifest client" | Out-Null
Assert-NativeExactPath -Path ([string]$manifest.CandidatePath) -Expected $ExpectedCandidatePath -Description "Manifest candidate" | Out-Null
if ([int]$manifest.Port -ne 4001) {
    throw "Invalid deployment manifest port."
}
if ([string]$manifest.CandidateHash -ne $ExpectedCandidateHash) {
    throw "Deployment manifest candidate hash does not match the approved hash."
}
if ([string]$manifest.Status -ne "Launched") {
    throw "Deployment status must be Launched before verification; current status is '$($manifest.Status)'."
}

$runtimeDll = Join-Path $ClientRoot "data\cache\localhost.4001\scripts.rust_dll.client.dll"
$cacheFile = Join-Path $ClientRoot "data\cache\localhost.4001.cache"
$clientExe = Join-Path $ClientRoot "FOnline.exe"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline -and -not (Test-Path -LiteralPath $runtimeDll)) {
    Start-Sleep -Milliseconds 500
}

Assert-NativePath -Path $cacheFile -Description "Port-specific cache"
Assert-NativePath -Path $runtimeDll -Description "Extracted Rust DLL"

$runtimeHash = Get-NativeSha256 -Path $runtimeDll
$hashPass = $runtimeHash -eq $ExpectedCandidateHash

$clientCanonical = Get-NativeCanonicalPath -Path $clientExe
$clientProcesses = @(
    Get-CimInstance Win32_Process | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_.ExecutablePath) -and
        (Get-NativeCanonicalPath -Path $_.ExecutablePath).Equals(
            $clientCanonical,
            [System.StringComparison]::OrdinalIgnoreCase)
    }
)

$loadedModule = $null
$moduleInspectionError = $null
foreach ($process in $clientProcesses) {
    try {
        $managedProcess = Get-Process -Id $process.ProcessId -ErrorAction Stop
        foreach ($module in $managedProcess.Modules) {
            if ($module.FileName.Equals($runtimeDll, [System.StringComparison]::OrdinalIgnoreCase)) {
                $loadedModule = [pscustomobject]@{
                    ProcessId = $process.ProcessId
                    Path = $module.FileName
                    BaseAddress = ("0x{0:X}" -f $module.BaseAddress.ToInt64())
                    ModuleMemorySize = $module.ModuleMemorySize
                }
                break
            }
        }
    }
    catch {
        $moduleInspectionError = $_.Exception.Message
    }

    if ($null -ne $loadedModule) {
        break
    }
}

$portListening = Test-NativeTcpPort -HostName "localhost" -Port $Port -TimeoutMs 1000
$cacheHash = Get-NativeSha256 -Path $cacheFile
$errorPatterns = @(
    "Main script section not found in MSG",
    "Invalid bind id<0>",
    "access violation",
    "missing export",
    "Failed to bind",
    "Cannot bind",
    "assertion failed"
)

$offsets = @{}
foreach ($entry in @($manifest.LogOffsets)) {
    $key = (Get-NativeCanonicalPath -Path ([string]$entry.Path)).ToLowerInvariant()
    $offsets[$key] = [int64]$entry.Length
}

$currentLogs = @(Get-NativeLogSnapshot -ServerRoot $ServerRoot -ClientRoot $ClientRoot)
$errorHits = @()
foreach ($log in $currentLogs) {
    $key = (Get-NativeCanonicalPath -Path ([string]$log.Path)).ToLowerInvariant()
    $offset = if ($offsets.ContainsKey($key)) { [int64]$offsets[$key] } else { 0 }
    $appendedText = Read-NativeAppendedText -Path ([string]$log.Path) -Offset $offset

    foreach ($pattern in $errorPatterns) {
        if ($appendedText.IndexOf($pattern, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $errorHits += [pscustomobject]@{
                File = [string]$log.Path
                Pattern = $pattern
                Offset = $offset
            }
        }
    }
}

$summary = [pscustomobject]@{
    VerifiedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    CandidateHashExpected = $ExpectedCandidateHash
    RuntimeDllPath = $runtimeDll
    RuntimeDllHash = $runtimeHash
    RuntimeHashMatches = $hashPass
    CachePath = $cacheFile
    CacheHash = $cacheHash
    Port = $Port
    PortListening = $portListening
    LoadedModule = $loadedModule
    ModuleInspectionError = $moduleInspectionError
    ErrorHits = $errorHits
}

$verifyPath = Join-Path $ServerRoot ("_native_echo_state\verify_" + (Get-NativeTimestamp) + ".json")
Write-NativeJson -InputObject $summary -Path $verifyPath

Write-Host ""
Write-Host "Native smoke verification"
Write-Host "Expected DLL hash:  $ExpectedCandidateHash"
Write-Host "Extracted DLL hash: $runtimeHash"
Write-Host "Hash match:         $hashPass"
Write-Host "Port 4001 listening:$portListening"

if ($null -ne $loadedModule) {
    Write-Host "Loaded module:      YES"
    Write-Host "  PID:              $($loadedModule.ProcessId)"
    Write-Host "  Path:             $($loadedModule.Path)"
    Write-Host "  Base:             $($loadedModule.BaseAddress)"
    Write-Host "  Memory size:      $($loadedModule.ModuleMemorySize)"
}
else {
    Write-Host "Loaded module:      NOT CONFIRMED"
    if ($moduleInspectionError) {
        Write-Host "  Inspection error: $moduleInspectionError"
    }
}

Write-Host "New error hits:     $($errorHits.Count)"
Write-Host "Report:             $verifyPath"

if (-not $hashPass -or -not $portListening -or $errorHits.Count -gt 0) {
    Write-Host ""
    Write-Host "VERIFY FAIL"
    exit 1
}

Write-Host ""
if ($null -ne $loadedModule) {
    Write-Host "VERIFY PASS"
    Write-Host "Native module confirmed."
    Write-Host "Ready for functional native-echo tests."
}
else {
    Write-Host "VERIFY READY WITH WARNING"
    Write-Host "PowerShell could not confirm the loaded module."
    Write-Host "Run the native-echo command as the definitive functional loading test."
}

exit 0
