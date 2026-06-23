from __future__ import annotations

import argparse
import time
from pathlib import Path


DEFAULT_CLIENT_ROOT = Path(r"C:\FOnlines\TLJ_CLIENT_LOCAL")
POLL_SECONDS = 0.25


def fake_translate(request_text: str) -> str:
    if request_text == "ping":
        return "pong from fake worker"
    return "[FAKE TRANSLATION] " + request_text


def read_request(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace").strip()


def write_response(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", errors="replace")


def watch(client_root: Path) -> None:
    mailbox_dir = client_root / "translation_bridge"
    request_path = mailbox_dir / "request.txt"
    response_path = mailbox_dir / "response.txt"
    last_request = None

    mailbox_dir.mkdir(parents=True, exist_ok=True)

    print(f"Client translation worker started")
    print(f"Client root: {client_root}")
    print(f"Watching: {request_path}")
    print(f"Writing: {response_path}")

    while True:
        request_text = read_request(request_path)
        if request_text and request_text != last_request:
            print(f"Request read: {request_text}")
            response_text = fake_translate(request_text)
            write_response(response_path, response_text)
            print(f"Response written: {response_text}")
            last_request = request_text

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
