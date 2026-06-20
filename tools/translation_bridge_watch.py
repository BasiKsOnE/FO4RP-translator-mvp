#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path


ENCODING = "cp1251"
DEFAULT_RESPONSE_MAILBOX = "logs/translation_bridge_responses.txt"
MYMEMORY_ENDPOINT = "https://api.mymemory.translated.net/get"
MYMEMORY_TIMEOUT_SECONDS = 5
TRANSLATION_BACKEND = os.environ.get("TRANSLATION_BACKEND", "fake").strip().lower()
TRANSLATION_ERROR_MODE = os.environ.get("TRANSLATION_ERROR_MODE", "fallback").strip().lower()


def parse_request(line: str) -> tuple[str, str, str, str, str, str, str, str] | None:
    line = line.rstrip("\r\n")
    if not line:
        return None

    parts = line.split("|", 7)
    if len(parts) != 8:
        print(f"Skipping malformed request: {line!r}", flush=True)
        return None

    return parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6], parts[7]


def translate_fake(direction: str, style: str, text: str) -> str:
    if direction == "en_to_ru":
        return "[RU BRIDGE TEST] " + text
    if direction == "ru_to_en":
        return "[EN BRIDGE TEST] " + text
    return text


def translate_mymemory(direction: str, style: str, text: str) -> str:
    # Free public test backend with public rate limits; not suitable for production.
    if direction == "en_to_ru":
        langpair = "en|ru"
    elif direction == "ru_to_en":
        langpair = "ru|en"
    else:
        raise ValueError(f"Unsupported translation direction for MyMemory: {direction}")

    query = urllib.parse.urlencode(
        {
            "q": text,
            "langpair": langpair,
        }
    )
    url = f"{MYMEMORY_ENDPOINT}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "FO4RP translation bridge"})

    with urllib.request.urlopen(request, timeout=MYMEMORY_TIMEOUT_SECONDS) as response:
        raw_body = response.read()

    body = raw_body.decode("utf-8", errors="replace")
    payload = json.loads(body)
    response_data = payload.get("responseData")
    if not isinstance(response_data, dict):
        raise ValueError("MyMemory response missing responseData")

    translated_text = response_data.get("translatedText")
    if not translated_text:
        raise ValueError("MyMemory response missing responseData.translatedText")

    return html.unescape(str(translated_text))


def translate_text(direction: str, style: str, text: str) -> str:
    # Real translation backends plug in here later. Keep backend functions pure:
    # direction/style/text in, translated text out.
    if TRANSLATION_BACKEND == "fake":
        return translate_fake(direction, style, text)
    if TRANSLATION_BACKEND == "mymemory":
        return translate_mymemory(direction, style, text)

    raise ValueError(f"Unsupported translation backend: {TRANSLATION_BACKEND}")


def safe_translate_text(direction: str, style: str, text: str) -> str | None:
    try:
        return translate_text(direction, style, text)
    except Exception as exc:
        print(f"Translation failed for direction={direction!r}, style={style!r}: {exc}", flush=True)
        if TRANSLATION_ERROR_MODE == "skip":
            return None
        return text


def write_response(
    path: Path, tick: str, crid: str, direction: str, style: str, volume: str, hex_x: str, hex_y: str, text: str
) -> None:
    translated_text = safe_translate_text(direction, style, text)
    if translated_text is None:
        print(f"Skipping response for tick={tick}, crid={crid}", flush=True)
        return

    with path.open("a", encoding=ENCODING, errors="replace") as responses:
        responses.write(f"{tick}|{crid}|{direction}|{style}|{volume}|{hex_x}|{hex_y}|{translated_text}\n")


def print_request(line: str, response_path: Path) -> None:
    parsed = parse_request(line)
    if parsed is None:
        return

    tick, crid, direction, style, volume, hex_x, hex_y, text = parsed
    print(f"tick={tick}", flush=True)
    print(f"crid={crid}", flush=True)
    print(f"direction={direction}", flush=True)
    print(f"style={style}", flush=True)
    print(f"volume={volume}", flush=True)
    print(f"hex_x={hex_x}", flush=True)
    print(f"hex_y={hex_y}", flush=True)
    print(f"text={text}", flush=True)
    print("", flush=True)
    write_response(response_path, tick, crid, direction, style, volume, hex_x, hex_y, text)


def watch(path: Path, response_path: Path, interval: float) -> None:
    print(f"Watching translation mailbox: {path}", flush=True)
    print(f"Writing translation responses to: {response_path}", flush=True)
    print(f"Translation backend: {TRANSLATION_BACKEND}", flush=True)
    print(f"Translation error mode: {TRANSLATION_ERROR_MODE}", flush=True)
    print(f"Mailbox encoding: {ENCODING}", flush=True)
    print("Starting at end of file; only new requests will be printed.", flush=True)

    position = 0
    initialized = False

    while True:
        if not path.exists():
            if initialized:
                position = 0
                initialized = False
            time.sleep(interval)
            continue

        with path.open("r", encoding=ENCODING, errors="replace") as mailbox:
            if not initialized:
                mailbox.seek(0, 2)
                position = mailbox.tell()
                initialized = True
            else:
                mailbox.seek(position)

            for line in mailbox:
                print_request(line, response_path)

            position = mailbox.tell()

        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch the FO4RP translation mailbox.")
    parser.add_argument(
        "mailbox",
        nargs="?",
        default="logs/translation_bridge_requests.txt",
        help="Path to the translation request mailbox.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.25,
        help="Polling interval in seconds.",
    )
    parser.add_argument(
        "--responses",
        default=DEFAULT_RESPONSE_MAILBOX,
        help="Path to the fake translation response mailbox.",
    )
    args = parser.parse_args()

    watch(Path(args.mailbox), Path(args.responses), args.interval)


if __name__ == "__main__":
    main()
