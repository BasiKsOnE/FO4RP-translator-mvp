#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time
from pathlib import Path


ENCODING = "cp1251"
DEFAULT_RESPONSE_MAILBOX = "logs/translation_bridge_responses.txt"


def parse_request(line: str) -> tuple[str, str, str, str, str] | None:
    line = line.rstrip("\r\n")
    if not line:
        return None

    parts = line.split("|", 4)
    if len(parts) != 5:
        print(f"Skipping malformed request: {line!r}", flush=True)
        return None

    return parts[0], parts[1], parts[2], parts[3], parts[4]


def write_fake_response(
    path: Path, tick: str, crid: str, direction: str, volume: str, text: str
) -> None:
    label = "[RU BRIDGE TEST]" if direction == "en_to_ru" else "[EN BRIDGE TEST]"
    translated_text = f"{label} {text}"

    with path.open("a", encoding=ENCODING, errors="replace") as responses:
        responses.write(f"{tick}|{crid}|{direction}|{volume}|{translated_text}\n")


def print_request(line: str, response_path: Path) -> None:
    parsed = parse_request(line)
    if parsed is None:
        return

    tick, crid, direction, volume, text = parsed
    print(f"tick={tick}", flush=True)
    print(f"crid={crid}", flush=True)
    print(f"direction={direction}", flush=True)
    print(f"volume={volume}", flush=True)
    print(f"text={text}", flush=True)
    print("", flush=True)
    write_fake_response(response_path, tick, crid, direction, volume, text)


def watch(path: Path, response_path: Path, interval: float) -> None:
    print(f"Watching translation mailbox: {path}", flush=True)
    print(f"Writing fake responses to: {response_path}", flush=True)
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
