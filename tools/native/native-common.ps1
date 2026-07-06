Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-NativeTimestamp {
    return (Get-Date).ToString("yyyyMMdd_HHmmss_fff")
}

function Get-NativeCanonicalPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "Path must not be empty."
    }

    return [System.IO.Path]::GetFullPath($Path).TrimEnd("\")
}

function Assert-NativePath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Description = "Required path"
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Description not found: $Path"
    }
}

function Assert-NativeExactPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Expected,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $actualFull = Get-NativeCanonicalPath -Path $Path
    $expectedFull = Get-NativeCanonicalPath -Path $Expected

    if (-not $actualFull.Equals(
        $expectedFull,
        [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Description must be $expectedFull; received $actualFull"
    }

    return $actualFull
}

function Assert-NativeContainedPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $childFull = Get-NativeCanonicalPath -Path $Path
    $parentFull = Get-NativeCanonicalPath -Path $Parent
    $prefix = $parentFull + "\"

    if ($childFull.Equals($parentFull, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not $childFull.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Description must be contained beneath $parentFull; received $childFull"
    }

    return $childFull
}

function Get-NativeSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    Assert-NativePath -Path $Path -Description "Hash target"
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Assert-NativeSha256 {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Expected
    )

    $actual = Get-NativeSha256 -Path $Path
    if ($actual -ne $Expected.ToUpperInvariant()) {
        throw "SHA-256 mismatch for $Path`nExpected: $Expected`nActual:   $actual"
    }
    return $actual
}

function Get-NativeTrackedChanges {
    param([Parameter(Mandatory = $true)][string]$RepoRoot)

    Assert-NativePath -Path (Join-Path $RepoRoot ".git") -Description "Git repository"
    $lines = @(& git -C $RepoRoot status --porcelain=v1 --untracked-files=no)
    if ($LASTEXITCODE -ne 0) {
        throw "git status failed in $RepoRoot"
    }

    $result = @()
    foreach ($line in $lines) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }

        $path = if ($line.Length -ge 4) { $line.Substring(3).Trim() } else { $line.Trim() }
        $result += [pscustomobject]@{
            Raw  = $line
            Path = $path.Replace("/", "\")
        }
    }
    return $result
}

function Assert-NativeRepoChanges {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [string[]]$AllowedPaths = @()
    )

    $allowed = @{}
    foreach ($path in $AllowedPaths) {
        $allowed[$path.Replace("/", "\").ToLowerInvariant()] = $true
    }

    $changes = @(Get-NativeTrackedChanges -RepoRoot $RepoRoot)
    $unexpected = @(
        $changes | Where-Object {
            -not $allowed.ContainsKey($_.Path.ToLowerInvariant())
        }
    )

    if ($unexpected.Count -gt 0) {
        $details = ($unexpected | ForEach-Object { $_.Raw }) -join "`n"
        throw "Unexpected tracked changes in ${RepoRoot}:`n$details"
    }

    return $changes
}

function Stop-NativeIsolatedProcesses {
    param([Parameter(Mandatory = $true)][string[]]$ExecutablePaths)

    $approved = @{}
    foreach ($path in $ExecutablePaths) {
        $canonical = Get-NativeCanonicalPath -Path $path
        $approved[$canonical.ToLowerInvariant()] = $canonical
    }

    $matches = @(
        Get-CimInstance Win32_Process | Where-Object {
            if ([string]::IsNullOrWhiteSpace($_.ExecutablePath)) {
                return $false
            }

            $canonical = Get-NativeCanonicalPath -Path $_.ExecutablePath
            return $approved.ContainsKey($canonical.ToLowerInvariant())
        }
    )

    foreach ($process in $matches) {
        Write-Host "Stopping PID $($process.ProcessId): $($process.ExecutablePath)"
        Stop-Process -Id $process.ProcessId -Force
    }

    return $matches
}

function Test-NativeTcpPort {
    param(
        [string]$HostName = "localhost",
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutMs = 500
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) {
            return $false
        }
        $client.EndConnect($async)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Wait-NativeTcpPort {
    param(
        [string]$HostName = "localhost",
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutSeconds = 90
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-NativeTcpPort -HostName $HostName -Port $Port -TimeoutMs 500) {
            return
        }
        Start-Sleep -Milliseconds 500
    }

    throw "Timed out waiting for ${HostName}:$Port"
}

function Invoke-NativeVcCommand {
    param(
        [Parameter(Mandatory = $true)][string]$VcVars32,
        [Parameter(Mandatory = $true)][string]$Command
    )

    Assert-NativePath -Path $VcVars32 -Description "vcvars32.bat"

    $temporaryBatch = Join-Path `
        ([System.IO.Path]::GetTempPath()) `
        ("tlj_native_vc_{0}_{1}.cmd" -f $PID, [Guid]::NewGuid().ToString("N"))

    $batchLines = @(
        "@echo off",
        ('call "{0}" >nul' -f $VcVars32),
        "if errorlevel 1 exit /b 1",
        $Command,
        "exit /b %errorlevel%"
    )

    $encoding = New-Object System.Text.ASCIIEncoding

    try {
        [System.IO.File]::WriteAllLines(
            $temporaryBatch,
            $batchLines,
            $encoding)

        & $temporaryBatch
        $exitCode = $LASTEXITCODE
    }
    finally {
        if (Test-Path -LiteralPath $temporaryBatch) {
            Remove-Item -LiteralPath $temporaryBatch -Force
        }
    }

    if ($exitCode -ne 0) {
        throw "Visual Studio command failed with exit code $exitCode`n$Command"
    }
}

function Write-NativeJson {
    param(
        [Parameter(Mandatory = $true)]$InputObject,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $parent = Split-Path -Parent $Path
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }

    $json = $InputObject | ConvertTo-Json -Depth 12
    $temporaryPath = "$Path.tmp.$PID.$([Guid]::NewGuid().ToString("N"))"
    $replacementBackupPath = $null
    $encoding = New-Object System.Text.UTF8Encoding($false)

    try {
        [System.IO.File]::WriteAllText($temporaryPath, $json, $encoding)
        if (Test-Path -LiteralPath $Path) {
            $replacementBackupPath = "$Path.replace-backup.$PID.$([Guid]::NewGuid().ToString("N"))"
            [System.IO.File]::Replace(
                $temporaryPath,
                $Path,
                $replacementBackupPath)
        }
        else {
            [System.IO.File]::Move($temporaryPath, $Path)
        }
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
        if ($replacementBackupPath -and
            (Test-Path -LiteralPath $replacementBackupPath)) {
            Remove-Item `
                -LiteralPath $replacementBackupPath `
                -Force `
                -ErrorAction SilentlyContinue
        }
    }
}

function Copy-NativeFileVerified {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    Assert-NativePath -Path $Source -Description "Copy source"
    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination -Force

    $sourceHash = Get-NativeSha256 -Path $Source
    $destinationHash = Get-NativeSha256 -Path $Destination
    if ($sourceHash -ne $destinationHash) {
        throw "Verified copy failed:`n$Source`n$Destination"
    }

    return $destinationHash
}

function Get-NativeDumpbinExportNames {
    param([Parameter(Mandatory = $true)][string]$Path)

    Assert-NativePath -Path $Path -Description "dumpbin export output"
    $names = @()
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^\s*\d+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+(\S+)') {
            $names += $Matches[1]
        }
    }
    return @($names | Sort-Object -Unique)
}

function Get-NativeLogSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$ServerRoot,
        [Parameter(Mandatory = $true)][string]$ClientRoot
    )

    $files = @()
    $messageBox = Join-Path $ClientRoot "messagebox"
    if (Test-Path -LiteralPath $messageBox) {
        $files += Get-ChildItem -LiteralPath $messageBox -Filter "messbox*.txt" -File -Recurse -ErrorAction SilentlyContinue
    }

    $files += Get-ChildItem -Path (Join-Path $ServerRoot "*.log") -File -ErrorAction SilentlyContinue
    $files += Get-ChildItem -Path (Join-Path $ServerRoot "*.txt") -File -ErrorAction SilentlyContinue

    $serverLogs = Join-Path $ServerRoot "logs"
    if (Test-Path -LiteralPath $serverLogs) {
        $files += Get-ChildItem -LiteralPath $serverLogs -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -in @(".log", ".txt") }
    }

    $seen = @{}
    $snapshot = @()
    foreach ($file in $files) {
        $canonical = Get-NativeCanonicalPath -Path $file.FullName
        $key = $canonical.ToLowerInvariant()
        if ($seen.ContainsKey($key)) {
            continue
        }
        $seen[$key] = $true
        $snapshot += [pscustomobject]@{
            Path = $canonical
            Length = [int64]$file.Length
            LastWriteTimeUtc = $file.LastWriteTimeUtc.ToString("o")
        }
    }
    return $snapshot
}

function Read-NativeAppendedText {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [int64]$Offset = 0
    )

    Assert-NativePath -Path $Path -Description "Log file"
    $stream = [System.IO.File]::Open(
        $Path,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::ReadWrite)

    try {
        if ($Offset -lt 0 -or $Offset -gt $stream.Length) {
            $Offset = 0
        }

        [void]$stream.Seek($Offset, [System.IO.SeekOrigin]::Begin)
        $remaining = [int]($stream.Length - $Offset)
        if ($remaining -le 0) {
            return ""
        }

        $bytes = New-Object byte[] $remaining
        $read = $stream.Read($bytes, 0, $remaining)
        return [System.Text.Encoding]::ASCII.GetString($bytes, 0, $read)
    }
    finally {
        $stream.Dispose()
    }
}
