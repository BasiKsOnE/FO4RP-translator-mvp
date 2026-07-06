# TLJ Client-Side Translator — Native Development Notes

## Purpose

This file is the source of truth for the native/DLL side of the TLJ RU↔EN client translator.

Do not repeat the historical DLL, toolchain, cache, or smoke-environment investigation unless one of those foundations changes.

## Goal

Make real local Argos translations available before FOnline renders an incoming message:

```text
AngelScript incoming callback
→ Rust client DLL
→ bounded localhost IPC
→ warmed Python 3.13 Argos worker
→ Rust returns translated string@
→ existing native FOnline presentation path
```

The backend must fail safely. On timeout or worker failure, the original source text remains unchanged. Backend errors must never appear as player dialogue.

## Current status

### FO4RP repository

- Root: `C:\FOnlines\FO4RP`
- Branch: `client-side-translator-mvp`
- Recorded HEAD before native-echo work:
  `7661cdd0afb3d8db09a84a5041d3888227735bdf`
- Remote used previously for pushes: `mine`
- Current intended tracked modifications:
  - `scripts\client_main.fos`
  - `scripts\rust_bindings.fos`

The native-echo source and tested DLL are intended to be committed atomically
after the native tooling/documentation commit.

### Recovered Rust workspace

- Probe root:
  `C:\FOnlines\RUST_DLL_BUILD_PROBE\20260705_013208`
- Workspace:
  `C:\FOnlines\RUST_DLL_BUILD_PROBE\20260705_013208\sacredcracker_rust_workspace`
- Historical revision:
  `54c91d71592334b95489d8ad914263041740a3bf`
- Local build metadata commit:
  `de0eb3dbb455a32b4a25d240a325a87110ad31b9`
- Local native-echo source commit:
  `688e7d2c6efc62e5a901c973bdcbb8a11a24a761`
- Toolchain:
  - Rust `1.57.0`
  - Cargo `1.57.0`
  - target `i686-pc-windows-msvc`
- Historical dependency lock was reconstructed in the probe.
- Native-echo Rust modifications:
  - `dll\common\src\state.rs`
  - `dll\common\src\engine_types\string.rs`
  - `dll\client\src\lib.rs`

### Native-echo candidate

- Path:
  `C:\FOnlines\RUST_DLL_BUILD_PROBE\20260705_013208\target_native_echo\i686-pc-windows-msvc\release\tnf_client_dll.dll`
- SHA-256:
  `6CDC3B704852DBEAE0F0625EB36BC02C612437D09CD207B0C5A2403BBA03FD98`
- PE32/x86
- Image size: `0x11C000`
- Export count: 29
- Added export: `NativeTranslationEcho`
- Original 28 exports preserved
- Runtime-tested successfully in the isolated localhost:4001 client.

The controlled native-echo milestone passed:

- isolated build;
- copied-server deployment;
- cache update and extraction;
- functional DLL-loading proof through `NativeTranslationEcho`;
- English and Cyrillic/CP1251 round trips;
- punctuation and repeated-space preservation;
- null/failure behavior;
- ordinary chat, translator-command, inventory, and interface regression checks;
- clean client exit;
- clean rollback.

Windows PowerShell could not confirm the loaded 32-bit module through process
module enumeration. The successful local echo export call provided definitive
functional proof that the candidate DLL was loaded. Module enumeration is
supplemental rather than mandatory for this legacy 32-bit client.

### Proven ABI pattern

The recovered Rust wrapper does not prove safe mutable `string& output` support.

The approved interface is:

```angelscript
string@ NativeTranslationEcho(string& input)
```

The Rust export returns an engine-created `ScriptString*` through the existing `AngelScriptApi::Script_String` mechanism. A null handle represents failure.

### Isolated smoke environment

- Server copy:
  `C:\FOnlines\FO4RP_DLL_SMOKE_SERVER`
- Client:
  `C:\FOnlines\TLJ_CLIENT_LOCAL`
- Test endpoint: `localhost:4001`
- Main/protected endpoint: `localhost:4000`

The isolated environment has passed:

- server startup
- script compilation
- DLL distribution
- cache update
- DLL extraction
- module loading
- login
- map loading
- movement
- inventory
- item and ground-object inspection
- PhysicalUI
- chat
- translator commands
- clean exit

### Critical cache rule

A fresh port-specific cache cannot bootstrap this legacy client.

The working `localhost.4001.cache` was seeded from:

```text
C:\FOnlines\TLJ_CLIENT_LOCAL\data\cache\localhost.4000.cache
```

Do not delete `localhost.4001.cache` during normal iteration.

To force updated native modules to extract, remove only:

```text
C:\FOnlines\TLJ_CLIENT_LOCAL\data\cache\localhost.4001\
```

The server then updates the seeded `.cache` payload, and FOnline recreates the extracted runtime directory.

Never modify or delete:

```text
localhost.4000.cache
localhost.4000\
5.161.90.132.4000\
```

## Known hashes

### Main/original Rust DLL

Path:

```text
C:\FOnlines\FO4RP\scripts\rust_dll\client.dll
```

SHA-256:

```text
95C244FEBDD6860966314108551096D70D664FC6177253E35AA1D28014452742
```

### Unchanged rebuilt DLL that passed runtime smoke testing

SHA-256:

```text
4636F533090D3BB91ABBDAC36459A37986685A49FDED1AB5D36045C95D3870F6
```

### Runtime-tested native-echo DLL

SHA-256:

```text
6CDC3B704852DBEAE0F0625EB36BC02C612437D09CD207B0C5A2403BBA03FD98
```

## Routine workflow

Use deterministic scripts instead of asking Codex to rediscover or manually repeat the environment.

```powershell
cd C:\FOnlines\FO4RP

.\tools\native\build-client-dll.ps1
.\tools\native\deploy-smoke.ps1
.\tools\native\verify-smoke.ps1
```

After manual testing:

```powershell
.\tools\native\rollback-smoke.ps1
```

The scripts are intentionally limited to the copied server and port-4001 client cache. Review them before first execution on the Windows machine.

`verify-smoke.ps1` performs pre-functional checks. If Windows PowerShell cannot
enumerate the loaded 32-bit module, execute the native-echo command as the
definitive functional DLL-loading test.

## Native-echo manual tests

```text
~tl_native_echo hello world
~tl_native_echo Привет, мир!
~tl_native_echo punctuation: !?.,;:'"()
~tl_native_echo multiple     spaces
~tl_native_echo
```

Expected:

```text
[NATIVE ECHO] hello world
[NATIVE ECHO] Привет, мир!
[NATIVE ECHO] punctuation: !?.,;:'"()
[NATIVE ECHO] multiple     spaces
[NATIVE ECHO ERROR]
```

Also verify:

- command is not sent as speech;
- no overhead speech bubble appears;
- no `[TL]` line appears;
- ordinary chat still works;
- one existing `~enru` or `~ruen` command still works;
- interfaces remain functional;
- client exits normally.

## Next milestone

Add a bounded synchronous localhost request, initially only ping/echo:

```text
AngelScript debug command
→ Rust DLL
→ localhost TCP
→ tiny Python echo server
→ Rust returns string@
→ local diagnostic line
```

Do not touch incoming chat or Argos during that milestone.

After TCP echo works:

1. add worker startup validation and single-instance binding;
2. add structured request/response framing;
3. add strict timeout and source fallback;
4. connect the warmed Argos backend;
5. measure latency;
6. only then wire the call into existing native translation helpers.

## Commit strategy

The Rust source and FO4RP source live in separate repositories/workspaces.

Before committing native work, decide how Rust source will be maintained:

1. fork and version the recovered Rust workspace; or
2. add a documented submodule/dependency arrangement; or
3. deliberately vendor a minimal reproducible native-source tree.

Do not commit only an unexplained DLL binary.

The reproducible build metadata and runtime-tested native-echo source now exist
as local commits in the recovered Rust repository. The recovered upstream
repository had no visible license. Do not push or otherwise publish that source
until ownership permission or licensing is resolved.

A reasonable eventual split is:

### Rust repository commit

- fallible state access;
- fallible CP1251 conversion;
- `NativeTranslationEcho`;
- reproducible lock/toolchain/build notes.

### FO4RP repository commit

- `rust_bindings.fos` binding;
- local debug command;
- build/deployment documentation;
- optionally the reviewed DLL according to project binary policy.

## Efficient Codex usage

Codex should be used for:

- narrow source investigation;
- proposed diffs;
- unsafe Rust/ABI review;
- compiler failures;
- targeted code changes.

PowerShell should handle:

- building;
- hashing;
- backups;
- deployment;
- launching;
- module verification;
- rollback.

A normal Codex prompt should be brief:

```text
Read TRANSLATOR_NATIVE_DEV.md.

Task: add one bounded localhost TCP echo export using the proven
NativeTranslationEcho string@ pattern.

PROPOSE A DIFF ONLY. Do not edit.

Scope:
- recovered Rust client source
- rust_bindings.fos
- one local debug command

Do not touch incoming chat, Argos, radio, AFK, narration, or file bridge.
```


## Automation safety revision

The first draft of the PowerShell kit was rejected before execution. Version 2 adds fixed path/port enforcement, exact executable matching, recoverable deployment states, preflighted rollback, append-only log inspection, and strict PE/export validation. Do not use the original draft.
