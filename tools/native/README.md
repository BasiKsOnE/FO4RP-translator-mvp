# Native translator helper scripts

These scripts automate the already-proven local smoke environment. They are not intended for a live/public server.

## Files

- `native-common.ps1` — shared safety, hash, process, port, and JSON helpers.
- `build-client-dll.ps1` — builds the recovered Rust x86 DLL using the historical toolchain.
- `deploy-smoke.ps1` — backs up and deploys only to the copied port-4001 server, preserves the seeded cache, and launches the isolated processes.
- `verify-smoke.ps1` — performs pre-functional extraction, hash, port, module-inspection, and common-log-error checks.
- `rollback-smoke.ps1` — restores the exact copied-server files and port-4001 cache recorded by the last deployment.

## FORP translation proxy runbook

Current prototype architecture:

```text
client.dll -> local TCP worker 127.0.0.1:33742 -> FORP translation proxy 127.0.0.1:8787 -> Google backend
```

Start the proxy first, then the local TCP worker. The proxy owns Google
credentials and shared translation cache/dedupe. The local worker should not
need Google credentials when `FORP_TRANSLATION_BACKEND=proxy`.

### Proxy environment

```text
GOOGLE_APPLICATION_CREDENTIALS=<local service-account JSON path>
FORP_PROXY_BACKEND=google
FORP_PROXY_HOST=127.0.0.1
FORP_PROXY_PORT=8787
FORP_PROXY_TOKEN=dev-token
FORP_PROXY_CACHE_MAX=4096
FORP_PROXY_CACHE_TTL_SECONDS=3600
```

Use the helper:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\native\start_translation_proxy.ps1 -Token dev-token -CredentialPath C:\path\to\local-google.json
```

Or start manually:

```powershell
$env:GOOGLE_APPLICATION_CREDENTIALS="C:\path\to\local-google.json"
$env:FORP_PROXY_BACKEND="google"
$env:FORP_PROXY_HOST="127.0.0.1"
$env:FORP_PROXY_PORT="8787"
$env:FORP_PROXY_TOKEN="dev-token"
py -3.13 tools\native\translation_proxy.py
```

For a no-credential smoke test:

```powershell
$env:FORP_PROXY_BACKEND="fake"
$env:FORP_PROXY_TOKEN="dev-token"
py -3.13 tools\native\translation_proxy.py
```

Direct proxy smoke:

```powershell
py -3.13 tools\native\proxy_smoke_test.py --token dev-token --direction en_to_ru --message "hello doctor" --verbose
```

### Worker environment

```text
FORP_TRANSLATION_BACKEND=proxy
FORP_PROXY_URL=http://127.0.0.1:8787/translate
FORP_PROXY_TOKEN=dev-token
FORP_PROXY_TIMEOUT_SECONDS=4
FORP_TCP_WORKERS=12
FORP_TCP_MAX_PENDING=0
FORP_TCP_BACKLOG=64
```

Use the helper:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\native\start_tcp_worker_proxy.ps1 -Token dev-token
```

If PowerShell says "running scripts is disabled on this system", use the
one-time `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ...` form.
Do not change global execution policy just for local dev testing.

Or start manually:

```powershell
$env:FORP_TRANSLATION_BACKEND="proxy"
$env:FORP_PROXY_URL="http://127.0.0.1:8787/translate"
$env:FORP_PROXY_TOKEN="dev-token"
$env:FORP_PROXY_TIMEOUT_SECONDS="4"
$env:FORP_TCP_WORKERS="12"
$env:FORP_TCP_MAX_PENDING="0"
$env:FORP_TCP_BACKLOG="64"
py -3.13 tools\native\tcp_echo_worker.py
```

Worker-through-proxy smoke:

```powershell
py -3.13 tools\native\tcp_load_test.py --operation 3 --count 10 --concurrency 10 --timeout 10 --message "I need a doctor at the clinic right now" --verbose
py -3.13 tools\native\tcp_load_test.py --operation 4 --count 10 --concurrency 10 --timeout 10 --message "мне нужен доктор в клинике прямо сейчас" --verbose
```

### Common failures

- `WinError 10061` or connection refused: proxy is not running, wrong
  `FORP_PROXY_URL`, or port `8787` is blocked.
- Auth mismatch: `FORP_PROXY_TOKEN` differs between proxy, worker, and smoke
  test.
- Google credentials missing: `GOOGLE_APPLICATION_CREDENTIALS` is unset or
  points to a missing file on the proxy machine.
- Stale DLL or cold request timeout: confirm the committed `client.dll` is
  deployed and increase only the local worker/proxy timeout for prototype
  testing if needed.
- Port already in use: stop the existing proxy or worker before starting
  another instance on `8787` or `33742`.

### Security

- Never ship Google service-account JSON to players.
- The proxy owns Google credentials; player machines should use the proxy
  backend without credential files.
- Use `FORP_PROXY_TOKEN` for any non-local or shared test.
- Public deployment requires firewalling plus TLS or a reverse proxy. The
  current helper scripts are dev/admin tools, not production service wrappers.

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
