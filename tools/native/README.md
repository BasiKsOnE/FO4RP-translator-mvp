# Native translator helper scripts

These scripts automate the already-proven local smoke environment. They are not intended for a live/public server.

## Files

- `native-common.ps1` — shared safety, hash, process, port, and JSON helpers.
- `build-client-dll.ps1` — builds the recovered Rust x86 DLL using the historical toolchain.
- `deploy-smoke.ps1` — backs up and deploys only to the copied port-4001 server, preserves the seeded cache, and launches the isolated processes.
- `verify-smoke.ps1` — performs pre-functional extraction, hash, port, module-inspection, and common-log-error checks.
- `rollback-smoke.ps1` — restores the exact copied-server files and port-4001 cache recorded by the last deployment.

## Install

Place this directory at:

```text
C:\FOnlines\FO4RP\tools\native
```

Place `TRANSLATOR_NATIVE_DEV.md` at:

```text
C:\FOnlines\FO4RP\TRANSLATOR_NATIVE_DEV.md
```

These will initially be untracked files.

## First review

Before the first execution, review all paths and hashes. The defaults are specific to the current machine and native-echo milestone.

The scripts deliberately refuse unexpected tracked FO4RP changes. They allow only:

```text
scripts\client_main.fos
scripts\rust_bindings.fos
```

## Use

From PowerShell:

```powershell
cd C:\FOnlines\FO4RP

Set-ExecutionPolicy -Scope Process Bypass

.\tools\native\build-client-dll.ps1
.\tools\native\deploy-smoke.ps1
.\tools\native\verify-smoke.ps1
```

Perform the manual game checks.

Then restore the copied environment:

```powershell
.\tools\native\rollback-smoke.ps1
```

The build, controlled localhost:4001 deployment, and rollback scripts have
passed the native-echo test. `verify-smoke.ps1` performs pre-functional checks.
When Windows PowerShell cannot enumerate the loaded 32-bit module, actual
execution of `NativeTranslationEcho` is the definitive DLL-loading test.

## Important safety rules

- All `.ps1` files are intentionally ASCII-only for Windows PowerShell 5.1 parsing.
- Visual Studio environment commands run through a temporary `.cmd` file to avoid nested `cmd.exe /c` quoting failures.
- Manifest updates are written atomically through a same-directory temporary file and a real File.Replace backup path for Windows PowerShell 5.1/.NET Framework.
- Rollback adds `RolledBackAtUtc` with `Add-Member -Force`, so older active manifests without that property remain recoverable.
- The build helper is locked to `target_native_echo`, `NativeTranslationEcho`, the known original DLL hash, and an exact `28 → 29` export transition.
- Never point these scripts at the main/public server.
- Never change the default server root to `C:\FOnlines\FO4RP`.
- Never delete `localhost.4000.cache`.
- Keep the seeded `localhost.4001.cache`.
- To refresh extraction, remove only `localhost.4001\`.
- The scripts do not commit or push.
- The scripts do not replace the main repository DLL.
- The scripts are machine-specific and remain locked to the documented paths
  and port `4001`.


## Enforced safety boundaries (v2)

- Paths are locked to the documented repository, copied server, isolated client, recovered probe, and port `4001`.
- Only the exact `FOnlineServer.exe` and `FOnline.exe` test executables may be stopped.
- Only one deployment may be active. Run rollback before deploying again.
- A recovery manifest is written after verified backups and before any active file is overwritten.
- Rollback reconstructs fixed destinations, verifies all backups before writing, restores the seeded cache, and removes `localhost.4001\` so FOnline can re-extract it cleanly.
- Verification reads only log bytes appended after deployment, including `ClientRoot\messagebox`.
