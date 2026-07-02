from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


DEFAULT_CLIENT_ROOT = Path(r"C:\FOnlines\TLJ_CLIENT_LOCAL")
POLL_SECONDS = 0.25
BRIDGE_ENCODING = "cp1251"
TRANSLATION_BACKEND = os.environ.get("TRANSLATION_BACKEND", "fake").lower()


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


def configure_argos_environment() -> None:
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")


def argos_translate(direction: str, text: str) -> str:
    if direction == "en_to_ru":
        source_code = "en"
        target_code = "ru"
    elif direction == "ru_to_en":
        source_code = "ru"
        target_code = "en"
    else:
        return "[ARGOS ERROR] unknown direction " + direction

    configure_argos_environment()

    try:
        from argostranslate import translate
    except ImportError:
        return "[ARGOS ERROR] argostranslate not installed"

    try:
        return translate.translate(text, source_code, target_code)
    except Exception as error:
        return "[ARGOS ERROR] " + str(error)


def translate_text(direction: str, text: str) -> str:
    if TRANSLATION_BACKEND == "fake":
        return fake_translate(direction, text)
    if TRANSLATION_BACKEND == "argos":
        return argos_translate(direction, text)
    return "[UNKNOWN BACKEND] " + text


def warmup_argos() -> None:
    if TRANSLATION_BACKEND != "argos":
        return

    configure_argos_environment()
    print("Warming up Argos...")
    start_time = time.perf_counter()

    try:
        from argostranslate import translate

        translate.translate("hello", "en", "ru")
        translate.translate("привет", "ru", "en")
        elapsed = time.perf_counter() - start_time
        print(f"Argos warmup complete in {elapsed:.3f}s")
    except Exception as error:
        print(f"Argos warmup failed: {error}")


def read_text_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_bytes().decode(BRIDGE_ENCODING, errors="replace").strip()


def write_response(
    path: Path,
    speaker: str,
    critter_id: str,
    say_type: str,
    direction: str,
    translated_text: str,
) -> None:
    response = (
        f"speaker={speaker}\n"
        f"critter_id={critter_id}\n"
        f"say_type={say_type}\n"
        f"direction={direction}\n"
        f"translated={translated_text}\n"
    )
    path.write_bytes(response.encode(BRIDGE_ENCODING, errors="replace"))


def watch(client_root: Path) -> None:
    mailbox_dir = client_root / "translation_bridge"
    requests_dir = mailbox_dir / "requests"
    responses_dir = mailbox_dir / "responses"

    requests_dir.mkdir(parents=True, exist_ok=True)
    responses_dir.mkdir(parents=True, exist_ok=True)

    print(f"Client translation worker started")
    print(f"Backend: {TRANSLATION_BACKEND}")
    print(f"Python: {sys.executable}")
    if TRANSLATION_BACKEND == "argos":
        configure_argos_environment()
        print(f"OMP_NUM_THREADS: {os.environ.get('OMP_NUM_THREADS', 'unset')}")
        print(f"MKL_NUM_THREADS: {os.environ.get('MKL_NUM_THREADS', 'unset')}")
        print(f"OPENBLAS_NUM_THREADS: {os.environ.get('OPENBLAS_NUM_THREADS', 'unset')}")
    print(f"Client root: {client_root}")
    print(f"Watching: {requests_dir}")
    print(f"Writing: {responses_dir}")

    warmup_argos()

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
            critter_id = request.get("critter_id", "")
            say_type = request.get("say_type", "")
            direction = request.get("direction", "")
            text = request.get("text", "")
            translation_start = time.perf_counter()
            response_text = translate_text(direction, text)
            translation_elapsed = time.perf_counter() - translation_start
            print(f"Translation time: {translation_elapsed:.3f}s")
            write_response(response_path, speaker, critter_id, say_type, direction, response_text)
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
