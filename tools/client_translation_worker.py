from __future__ import annotations

import argparse
import time
from pathlib import Path


DEFAULT_CLIENT_ROOT = Path(r"C:\FOnlines\TLJ_CLIENT_LOCAL")
POLL_SECONDS = 0.25


def parse_record(raw_text: str) -> dict[str, str]:
    record: dict[str, str] = {}
    for line in raw_text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            record[key.strip()] = value
    return record


def fake_translate(direction: str, text: str) -> str:
    if direction == "en_to_ru":
        return "[RU FILE TEST] " + text
    if direction == "ru_to_en":
        return "[EN FILE TEST] " + text
    return "[UNKNOWN FILE TEST] " + text


def read_text_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace").strip()


def write_response(path: Path, speaker: str, direction: str, translated_text: str) -> None:
    path.write_text(
        f"speaker={speaker}\ndirection={direction}\ntranslated={translated_text}\n",
        encoding="utf-8",
        errors="replace",
    )


def watch(client_root: Path) -> None:
    mailbox_dir = client_root / "translation_bridge"
    requests_dir = mailbox_dir / "requests"
    responses_dir = mailbox_dir / "responses"

    requests_dir.mkdir(parents=True, exist_ok=True)
    responses_dir.mkdir(parents=True, exist_ok=True)

    print(f"Client translation worker started")
    print(f"Client root: {client_root}")
    print(f"Watching: {requests_dir}")
    print(f"Writing: {responses_dir}")

    while True:
        for request_path in sorted(requests_dir.glob("*.txt")):
            response_path = responses_dir / request_path.name
            if response_path.exists():
                continue

            request_text = read_text_file(request_path)
            if not request_text:
                continue

            print(f"Request read: {request_text}")
            request = parse_record(request_text)
            speaker = request.get("speaker", "Test")
            direction = request.get("direction", "")
            text = request.get("text", "")
            response_text = fake_translate(direction, text)
            write_response(response_path, speaker, direction, response_text)
            print(f"Response written: {response_path}: {response_text}")

        time.sleep(POLL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake client-side translation mailbox worker.")
    parser.add_argument(
        "--client-root",
        type=Path,
        default=DEFAULT_CLIENT_ROOT,
        help=f"Client root directory. Defaults to {DEFAULT_CLIENT_ROOT}",
    )
    args = parser.parse_args()

    watch(args.client_root)


if __name__ == "__main__":
    main()
