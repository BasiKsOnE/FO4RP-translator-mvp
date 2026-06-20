# TLJ Live Chat Translation MVP

This branch contains a proof-of-concept live RU/EN chat translation bridge for TLJ / FOnline.

The MVP currently uses fake translation output (`[RU BRIDGE TEST]` / `[EN BRIDGE TEST]`) to prove the plumbing, chat routing, style preservation, and per-player language view before wiring in a real translation backend.

## What this branch proves

This branch proves:

* Server-side chat interception for normal RP chat.
* A file-mailbox bridge from FOnline scripts to an external Python process.
* CP1251-safe RU/EN text handling for TLJ/FOnline chat text.
* A request/response translation protocol.
* Translation echo support for:

  * normal speech
  * volume-prefixed speech (`//N`)
  * whisper (`/w`, `//1`, `//2`, `//3`)
  * loud speech (`//50`)
  * shout (`/s`)
  * emote (`/e`, `*text*`)
  * depersonalized narration (`/n`)
* Native chat rendering for non-narration translated messages.
* Correct `/n` narration locus preservation.
* Per-player translation preferences via chat commands.

## Player commands

Players can toggle translation directions independently.

```text
~enru 1
```

Enable English-to-Russian translated chat.

```text
~enru 0
```

Disable English-to-Russian translated chat.

```text
~ruen 1
```

Enable Russian-to-English translated chat.

```text
~ruen 0
```

Disable Russian-to-English translated chat.

Example use cases:

* English-speaking player: `~ruen 1`, `~enru 0`
* Russian-speaking player: `~enru 1`, `~ruen 0`
* Bilingual player who wants original chat only: both off

## How the language view works

The first attempt used targeted client-side display, but that could not reproduce true FOnline speech behavior. It behaved like static map/narration text instead of real chat.

The current MVP uses the native chat system for non-`/n` messages:

1. Original chat is intercepted server-side.
2. A translation request is written to a mailbox file.
3. The Python bridge worker reads the request.
4. The Python bridge worker writes a fake translated response.
5. The server emits the translated message as a real native chat message from the original critter.
6. Hidden bridge metadata marks the message as translated.
7. Each client filters original/translated packets locally based on that player’s `~enru` / `~ruen` flags.

This gives translated speech the same native behavior as normal TLJ chat:

* follows the speaker’s head
* uses the real chat bubble system
* respects speech volume behavior
* keeps speaker attribution in the chat log
* preserves whisper/loud/emote/shout behavior

`/n` narration is the exception. Since narration is already custom fixed-locus map text, translated narration continues to use the existing narration display path and preserves the original narration hex location.

## Translation bridge protocol

The server writes translation requests to:

```text
logs\translation_bridge_requests.txt
```

The Python bridge writes responses to:

```text
logs\translation_bridge_responses.txt
```

The current protocol is:

```text
tick|crid|direction|style|volume|hexX|hexY|text
```

Fields:

* `tick` — server tick for tracing
* `crid` — original speaker critter id
* `direction` — `en_to_ru` or `ru_to_en`
* `style` — chat style
* `volume` — speech volume where applicable
* `hexX` — speaker/narration X hex
* `hexY` — speaker/narration Y hex
* `text` — text to translate or translated text

Supported styles:

```text
speech
whisper
loud
shout
emote
narration
```

## Python bridge worker

New file:

```text
tools/translation_bridge_watch.py
```

Run from the repo root:

```powershell
python tools\translation_bridge_watch.py
```

The worker currently:

* watches `logs\translation_bridge_requests.txt`
* starts at the end of the file
* reads mailbox text using CP1251
* parses the translation request protocol
* writes fake translated responses to `logs\translation_bridge_responses.txt`

It does not currently call a real translation API.

## Modified files

### `scripts/rp_chat.fos`

Adds the main server-side translation bridge logic:

* writes translation requests
* processes translation responses
* preserves chat style and volume
* handles `/n` narration location
* emits translated non-narration chat through native `cr.Say(...)`
* supports shout-specific behavior matching TLJ’s `/s` path
* suppresses original far-listener shout netmsgs when translated shout is enabled

### `scripts/client_main.fos`

Adds:

* `~enru` / `~ruen` command parsing
* client-side translation packet filtering
* hidden bridge metadata stripping
* original-message suppression when a translated view is enabled

### `scripts/_defines.fos`

Adds persistent player flags:

```cpp
PLAYER_FLAG_TRANSLATE_ENRU
PLAYER_FLAG_TRANSLATE_RUEN
```

### `scripts/loop.fos`

Adds polling for translation bridge response processing.

### `scripts/chat.fos`

Earlier proof hook was removed/cleaned up. The branch now relies on the server bridge and client-side filtering architecture.

### `tools/translation_bridge_watch.py`

Adds the local Python bridge worker.

## Current limitations

This is still an MVP.

Known limitations:

* Translation is fake; real API/local translator integration is not implemented yet.
* The bridge worker must be running externally.
* If a player has translation enabled and the bridge fails, they may temporarily miss the original message because the client suppresses originals while waiting for translated packets.
* The file mailbox protocol is simple and works for proof-of-concept, but a production version should use safer escaping, queuing, locking, or a native bridge.
* Real translation should be added behind a clean function in the Python worker, with error fallback and rate limiting.

## Next steps

Recommended next steps:

1. Replace fake `[RU BRIDGE TEST]` / `[EN BRIDGE TEST]` output with a real translation backend.
2. Add API key/config handling outside the repo.
3. Add fallback behavior if translation fails.
4. Add rate limiting and duplicate suppression.
5. Consider replacing the file mailbox with a native DLL or server bridge if performance becomes an issue.
6. Polish player-facing command help text.
