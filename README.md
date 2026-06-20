# TLJ Live Chat Translation MVP

This branch is a proof-of-concept for live RU/EN chat translation in TLJ / FO4RP.

It currently proves that translated chat can be routed back into the normal in-game chat flow, including overhead speech text, whispers, shouts, emotes, narration, Cyrillic display, and per-player language view toggles.

The branch now supports both:

* `fake` backend: test labels only, no internet required.
* `mymemory` backend: free public translation API for real RU/EN translation testing.

This is still an MVP. The current implementation is built to prove the chat plumbing first, not final production architecture.

---

## Current status

Working:

* English to Russian translation.
* Russian to English translation.
* Normal overhead speech bubbles.
* Whisper formatting.
* Shout formatting.
* Emote formatting.
* `/n` narration location handling.
* CP1251-safe RU/EN mailbox text handling.
* Per-player language view toggles:

  * `~enru 1`
  * `~enru 0`
  * `~ruen 1`
  * `~ruen 0`
* Configurable Python translation worker backend:

  * `fake`
  * `mymemory`

Not final:

* The current translation bridge uses a server-side file mailbox and an external Python worker.
* MyMemory is a free public test backend and should not be treated as production infrastructure.
* Translation quality may vary.
* Text sent through the MyMemory backend is sent to a public third-party service.
* Production should likely move toward a native client-side library or a configurable native library that can support both cloud and local translation backends.

---

## Branch purpose

This branch proves the following pipeline:

```txt
player chat
→ server chat hook
→ translation request mailbox
→ Python translation worker
→ translation response mailbox
→ server emits translated line
→ client filters original/translated view
→ player sees preferred language in normal chat flow
```

The important proof is not the translation provider. The important proof is that the game can display translated chat in a usable way.

---

## Files changed

Main script changes:

```txt
scripts/_defines.fos
scripts/chat.fos
scripts/client_main.fos
scripts/loop.fos
scripts/rp_chat.fos
tools/translation_bridge_watch.py
```

Documentation:

```txt
TRANSLATION_MVP_README.md
```

---

## Private server setup

These instructions assume a local/private FO4RP server checkout on Windows.

Example repo path:

```cmd
C:\FOnlines\FO4RP
```

Open a terminal:

```cmd
cd C:\FOnlines\FO4RP
```

Make sure you are on the MVP branch:

```cmd
git checkout translator-mvp
```

Start the local server:

```cmd
FOnlineServer.exe -start
```

If the server expects `FOnlineServer.cfg` and your checkout only has `tnfFOnlineServer.cfg`, copy it:

```cmd
copy tnfFOnlineServer.cfg FOnlineServer.cfg
```

Then start the server again:

```cmd
FOnlineServer.exe -start
```

Connect with a local client as usual.

---

## Starting the translation worker

The translation worker is:

```txt
tools/translation_bridge_watch.py
```

It watches:

```txt
logs\translation_bridge_requests.txt
```

And writes:

```txt
logs\translation_bridge_responses.txt
```

Start it from the repo root.

Default fake backend:

```cmd
cd C:\FOnlines\FO4RP
python tools\translation_bridge_watch.py
```

Expected startup output:

```txt
Watching translation mailbox: logs\translation_bridge_requests.txt
Writing translation responses to: logs\translation_bridge_responses.txt
Translation backend: fake
Translation error mode: fallback
Mailbox encoding: cp1251
Starting at end of file; only new requests will be printed.
```

With the fake backend, translations are test labels:

```txt
[RU BRIDGE TEST] ...
[EN BRIDGE TEST] ...
```

---

## Testing real translation with MyMemory

The MyMemory backend uses the public MyMemory REST endpoint anonymously by default.

No API key, token, maintainer account, or BasiKsOnE email address is included in this repo. If someone runs this on their own private server, the MyMemory requests come from that server, machine, and network, not from BasiKsOnE.

MyMemory public anonymous usage has a limited free quota, currently documented as 5,000 chars/day. MyMemory also documents a higher free quota, currently 50,000 chars/day, when a `de=` email parameter is provided. This MVP does not send `de=` by default.

Server operators should check MyMemory's current usage limits and terms before relying on it. Treat this as a free public test backend, not production infrastructure.

Do not use this for private or sensitive text. It is for proof-of-concept testing only.

In Command Prompt:

```cmd
cd C:\FOnlines\FO4RP
set TRANSLATION_BACKEND=mymemory
python tools\translation_bridge_watch.py
```

Expected startup output:

```txt
Translation backend: mymemory
Translation error mode: fallback
```

In PowerShell, use:

```powershell
cd C:\FOnlines\FO4RP
$env:TRANSLATION_BACKEND="mymemory"
python tools\translation_bridge_watch.py
```

To reset the backend in Command Prompt:

```cmd
set TRANSLATION_BACKEND=
```

To reset the backend in PowerShell:

```powershell
Remove-Item Env:\TRANSLATION_BACKEND
```

---

## Player commands

Players can choose which direction they want translated.

Enable English to Russian:

```txt
~enru 1
```

Disable English to Russian:

```txt
~enru 0
```

Enable Russian to English:

```txt
~ruen 1
```

Disable Russian to English:

```txt
~ruen 0
```

Example testing setup:

* English-speaking player enables `~ruen 1`.
* Russian-speaking player enables `~enru 1`.

The client filtering logic controls which original/translated lines each player sees.

---

## Basic test checklist

After starting the server and translation worker, test:

Normal English speech:

```txt
hello
```

Normal Russian speech:

```txt
привет
```

Whisper:

```txt
/w hello
```

Shout:

```txt
/s hello
```

Emote:

```txt
*checks the wound carefully*
```

Narration:

```txt
/n A cold wind moves through the street.
```

Volume speech:

```txt
//50 hello
```

Expected result:

* Fake backend shows `[RU BRIDGE TEST]` / `[EN BRIDGE TEST]`.
* MyMemory backend shows real translated text.
* Chat formatting should remain appropriate for the original style.

---

## Backend configuration

The worker supports environment variables.

```txt
TRANSLATION_BACKEND
```

Supported values:

```txt
fake
mymemory
```

Default:

```txt
fake
```

```txt
TRANSLATION_ERROR_MODE
```

Supported values:

```txt
fallback
skip
```

Default:

```txt
fallback
```

Behavior:

* `fallback`: if translation fails, write the original text as the translated response.
* `skip`: if translation fails, write no response.

Example:

```cmd
set TRANSLATION_BACKEND=mymemory
set TRANSLATION_ERROR_MODE=fallback
python tools\translation_bridge_watch.py
```

---

## Current implementation notes

The current MVP uses server-side chat interception because that was the fastest way to prove the whole chat-routing and display pipeline.

The current flow is:

```txt
rp_chat.fos
→ request mailbox
→ translation_bridge_watch.py
→ response mailbox
→ rp_chat.fos response processor
→ cr.Say / native chat emission
→ client_main.fos filtering
```

The client-side filtering exists because the server currently emits translated bridge messages back into normal chat, and each client decides whether to see the original line or the translated line.

This gives the MVP native-looking chat behavior without requiring a client DLL yet.

---

## Notes from Apeshii / future architecture

Apeshii pointed out that ideally this should not add avoidable work to the server.

A better production direction may be:

```txt
server sends normal chat only
→ client receives incoming chat
→ client decides whether translation is needed
→ client native library calls configured translation backend
→ client displays translated text locally
```

Possible production architecture:

```txt
native client-side translation library
→ AngelScript bindings
→ configurable backend
   → cloud translation service
   → local translation service
```

Another possible architecture:

```txt
single native translation library
→ supports both cloud and local translation
→ bound into AngelScript
→ usable from client-side scripts
```

This would reduce server workload and allow player-specific translation preferences to live mostly client-side.

The main technical question to answer next is whether client-side delayed/asynchronous translation can still display as proper overhead chat text rather than only as map text or overlay text.

---

## Future roadmap

Near-term:

1. Keep `fake` backend as the safe default.
2. Keep `mymemory` backend for free real-translation testing.
3. Improve error handling and logging around public backend failures.
4. Add simple rate limiting / cooldown protection.
5. Add optional operator-provided MyMemory email/config support.
6. Add docs for backend setup and privacy warnings.

Mid-term:

1. Investigate client-side translation path.
2. Confirm whether client-side code can suppress, replace, or locally re-display incoming chat as proper overhead speech.
3. Prototype a native client-side library with AngelScript bindings.
4. Prototype local/self-hosted translation backend support.
5. Support both local and cloud translation providers through one interface.

Long-term:

1. Replace Python/file mailbox bridge if production needs require it.
2. Move translation workload away from server if client-side native display is viable.
3. Add configurable provider support:

   * free public API for testing
   * local model / local translation service
   * optional paid cloud provider
4. Add proper configuration file support.
5. Add server/operator documentation.
6. Add player-facing documentation for translation toggles.

---

## Privacy warning

The `mymemory` backend sends chat text to a public third-party translation service.

By default, this repo sends anonymous public MyMemory requests. It does not include a BasiKsOnE API key, token, email address, or account. Traffic belongs to whoever runs the worker: their server, machine, and network make the requests.

Public anonymous MyMemory usage is limited, currently documented as 5,000 chars/day. MyMemory currently documents 50,000 chars/day when a `de=` email parameter is provided, but this MVP does not send that parameter by default.

Server operators should check MyMemory's current limits and terms before enabling it for a community. It is useful for free proof-of-concept testing, not as production translation infrastructure.

For private testing, sensitive RP, staff-only text, or production use, prefer a local translation backend or a trusted self-hosted service.

---

## Summary

This branch demonstrates that live RU/EN translation is technically possible inside TLJ/FO4RP chat.

The current version is a working proof-of-concept, not a final architecture.

The important result is that translated lines can appear in the normal in-game chat experience while preserving style, Cyrillic text, and player-specific language preferences.
