[CmdletBinding()]
param(
    [string]$RepoRoot = "C:\FOnlines\FO4RP",
    [string]$ProbeRoot = "C:\FOnlines\RUST_DLL_BUILD_PROBE\20260705_013208",
    [string]$TargetName = "target_native_echo",
    [string]$VcVars32 = "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars32.bat",
    [string]$RequiredExport = "NativeTranslationEcho"
)

. (Join-Path $PSScriptRoot "native-common.ps1")

$expectedRepoRoot = "C:\FOnlines\FO4RP"
$expectedProbeRoot = "C:\FOnlines\RUST_DLL_BUILD_PROBE\20260705_013208"
Assert-NativeExactPath -Path $RepoRoot -Expected $expectedRepoRoot -Description "Repository" | Out-Null
Assert-NativeExactPath -Path $ProbeRoot -Expected $expectedProbeRoot -Description "Probe root" | Out-Null

if ($TargetName -ne "target_native_echo") {
    throw "TargetName must be target_native_echo."
}
if ($RequiredExport -ne "NativeTranslationEcho") {
    throw "RequiredExport must be NativeTranslationEcho."
}

$rustWorkspace = Join-Path $ProbeRoot "sacredcracker_rust_workspace"
$rustClientDir = Join-Path $rustWorkspace "dll\client"
$rustupHome = Join-Path $ProbeRoot "rustup-home"
$cargoHome = Join-Path $ProbeRoot "cargo-home-historical"
$targetDir = Join-Path $ProbeRoot $TargetName
$toolchainBin = Join-Path $rustupHome "toolchains\1.57.0-x86_64-pc-windows-msvc\bin"
$cargoExe = Join-Path $toolchainBin "cargo.exe"
$candidate = Join-Path $targetDir "i686-pc-windows-msvc\release\tnf_client_dll.dll"
$referenceDll = Join-Path $RepoRoot "scripts\rust_dll\client.dll"
$metadataDir = Join-Path $ProbeRoot ("native_build_metadata\" + (Get-NativeTimestamp))

Assert-NativePath -Path $rustClientDir -Description "Recovered Rust client crate"
Assert-NativePath -Path $rustupHome -Description "Recovered rustup home"
Assert-NativePath -Path $cargoHome -Description "Historical Cargo home"
Assert-NativePath -Path $cargoExe -Description "Rust 1.57 cargo.exe"
Assert-NativePath -Path $VcVars32 -Description "Visual Studio x86 environment"
Assert-NativePath -Path $referenceDll -Description "Reference Rust DLL"
Assert-NativeSha256 `
    -Path $referenceDll `
    -Expected "95C244FEBDD6860966314108551096D70D664FC6177253E35AA1D28014452742" |
    Out-Null

$changes = @(Assert-NativeRepoChanges `
    -RepoRoot $RepoRoot `
    -AllowedPaths @("scripts\client_main.fos", "scripts\rust_bindings.fos"))

Write-Host "FO4RP tracked changes:"
if ($changes.Count -eq 0) {
    Write-Host "  none"
}
else {
    $changes | ForEach-Object { Write-Host "  $($_.Raw)" }
}

New-Item -ItemType Directory -Force -Path $metadataDir | Out-Null

$oldRustupHome = $env:RUSTUP_HOME
$oldCargoHome = $env:CARGO_HOME
$oldTargetDir = $env:CARGO_TARGET_DIR
$oldPath = $env:PATH

try {
    $env:RUSTUP_HOME = $rustupHome
    $env:CARGO_HOME = $cargoHome
    $env:CARGO_TARGET_DIR = $targetDir
    $env:PATH = "$toolchainBin;$oldPath"

    Push-Location $rustClientDir
    try {
        Write-Host "Building x86 Rust client DLL..."
        $cargoCommand = '"{0}" build --release --target i686-pc-windows-msvc --locked -v' -f $cargoExe
        Invoke-NativeVcCommand -VcVars32 $VcVars32 -Command $cargoCommand
    }
    finally {
        Pop-Location
    }
}
finally {
    $env:RUSTUP_HOME = $oldRustupHome
    $env:CARGO_HOME = $oldCargoHome
    $env:CARGO_TARGET_DIR = $oldTargetDir
    $env:PATH = $oldPath
}

Assert-NativePath -Path $candidate -Description "Built DLL"

$candidateExportsPath = Join-Path $metadataDir "candidate-exports.txt"
$referenceExportsPath = Join-Path $metadataDir "reference-exports.txt"
$headersPath = Join-Path $metadataDir "candidate-headers.txt"

Invoke-NativeVcCommand -VcVars32 $VcVars32 -Command ('dumpbin /nologo /exports "{0}" > "{1}"' -f $candidate, $candidateExportsPath)
Invoke-NativeVcCommand -VcVars32 $VcVars32 -Command ('dumpbin /nologo /exports "{0}" > "{1}"' -f $referenceDll, $referenceExportsPath)
Invoke-NativeVcCommand -VcVars32 $VcVars32 -Command ('dumpbin /nologo /headers "{0}" > "{1}"' -f $candidate, $headersPath)

$candidateExports = @(Get-NativeDumpbinExportNames -Path $candidateExportsPath)
$referenceExports = @(Get-NativeDumpbinExportNames -Path $referenceExportsPath)
$headersText = Get-Content -LiteralPath $headersPath -Raw

if ($candidateExports -notcontains $RequiredExport) {
    throw "Required export not found: $RequiredExport"
}

$missingReferenceExports = @($referenceExports | Where-Object { $candidateExports -notcontains $_ })
if ($missingReferenceExports.Count -gt 0) {
    throw "Candidate is missing reference exports: $($missingReferenceExports -join ', ')"
}

$unexpectedExports = @($candidateExports | Where-Object {
    $_ -ne $RequiredExport -and $referenceExports -notcontains $_
})
if ($unexpectedExports.Count -gt 0) {
    throw "Candidate has unexpected exports: $($unexpectedExports -join ', ')"
}

if ($referenceExports.Count -ne 28) {
    throw "Expected exactly 28 reference exports; found $($referenceExports.Count)."
}
if ($candidateExports.Count -ne 29) {
    throw "Expected exactly 29 candidate exports; found $($candidateExports.Count)."
}

if ($headersText -notmatch '(?im)^\s*14C machine \(x86\)') {
    throw "Candidate is not PE x86 (14C)."
}
if ($headersText -notmatch '(?im)^\s*10B magic # \(PE32\)') {
    throw "Candidate is not PE32 (10B)."
}

$hash = Get-NativeSha256 -Path $candidate
$file = Get-Item -LiteralPath $candidate

$summary = [pscustomobject]@{
    BuiltAtUtc = (Get-Date).ToUniversalTime().ToString("o")
    CandidatePath = $candidate
    Sha256 = $hash
    FileSizeBytes = $file.Length
    Architecture = "PE32 x86"
    RequiredExport = $RequiredExport
    CandidateExportCount = $candidateExports.Count
    ReferenceExportCount = $referenceExports.Count
    ExportVerified = $true
    RustWorkspace = $rustWorkspace
    RustClientDir = $rustClientDir
    TargetDirectory = $targetDir
    MetadataDirectory = $metadataDir
}

$summaryPath = Join-Path $metadataDir "build-summary.json"
Write-NativeJson -InputObject $summary -Path $summaryPath

Write-Host ""
Write-Host "BUILD PASS"
Write-Host "DLL:       $candidate"
Write-Host "SHA256:    $hash"
Write-Host "Size:      $($file.Length) bytes"
Write-Host "Arch:      PE32 x86"
Write-Host "Exports:   $($candidateExports.Count)"
Write-Host "New export:$RequiredExport"
Write-Host "Meta:      $metadataDir"
