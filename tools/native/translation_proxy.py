#!/usr/bin/env python3
from __future__ import annotations

import hmac
import html
import importlib.util
import json
import os
import re
import sys
import threading
import time
import urllib.request
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


BACKEND_GOOGLE = "google"
BACKEND_FAKE = "fake"
DEFAULT_BACKEND = BACKEND_GOOGLE
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_CACHE_MAX = 4096
DEFAULT_CACHE_TTL_SECONDS = 3600.0
PREVIEW_LIMIT = 120
MAX_REQUEST_BYTES = 65536
THREAD_LOCAL = threading.local()
TRANSLATION_CACHE_LOCK = threading.Lock()
TRANSLATION_CACHE: OrderedDict[tuple[str, str, str, str], tuple[float, str]] = OrderedDict()
IN_FLIGHT_TRANSLATIONS: dict[tuple[str, str, str, str], "InFlightTranslation"] = {}
VALID_BACKENDS = (BACKEND_GOOGLE, BACKEND_FAKE)


class InFlightTranslation:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.result = ""
        self.error = ""


def preview_text(text: str, limit: int = PREVIEW_LIMIT) -> str:
    text = text.replace("\r", " ").replace("\n", " ")
    if len(text) > limit:
        text = text[:limit] + "..."
    return text.encode("unicode_escape", errors="replace").decode("ascii")


def read_int_env(name: str, default: int, minimum: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return value if value >= minimum else default


def read_float_env(name: str, default: float, minimum: float) -> float:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError:
        return default
    return value if value >= minimum else default


def normalize_language_code(raw_code: object, field_name: str) -> str:
    if not isinstance(raw_code, str):
        raise ValueError(f"{field_name} must be a string")
    value = raw_code.strip()
    if not value:
        raise ValueError(f"{field_name} is empty")
    if len(value) > 20:
        raise ValueError(f"{field_name} is too long")
    normalized = value.lower()
    if not re.fullmatch(r"[a-z0-9-]{1,20}", normalized):
        raise ValueError(f"{field_name} contains unsupported characters")
    return normalized


def google_dependency_present() -> bool:
    try:
        return importlib.util.find_spec("google.cloud.translate_v2") is not None
    except ModuleNotFoundError:
        return False


def google_credential_status() -> str:
    credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_path:
        return "not_set"
    if os.path.isfile(credentials_path):
        return "file_exists"
    return "file_missing"


BACKEND = os.environ.get("FORP_PROXY_BACKEND", DEFAULT_BACKEND).strip().lower()
if BACKEND not in VALID_BACKENDS:
    print(
        f"WARNING: FORP_PROXY_BACKEND={BACKEND!r} is invalid; defaulting to {DEFAULT_BACKEND}",
        flush=True,
    )
    BACKEND = DEFAULT_BACKEND
HOST = os.environ.get("FORP_PROXY_HOST", DEFAULT_HOST)
PORT = read_int_env("FORP_PROXY_PORT", DEFAULT_PORT, 1)
CACHE_MAX = read_int_env("FORP_PROXY_CACHE_MAX", DEFAULT_CACHE_MAX, 1)
CACHE_TTL_SECONDS = read_float_env("FORP_PROXY_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS, 0.1)
TOKEN = os.environ.get("FORP_PROXY_TOKEN", "").strip()
AUTH_ENABLED = bool(TOKEN)


def log_diag(event: str, **fields: object) -> None:
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    if details:
        print(f"[FORP PROXY DIAG] ts={timestamp} event={event} {details}", flush=True)
    else:
        print(f"[FORP PROXY DIAG] ts={timestamp} event={event}", flush=True)


class GoogleTranslator:
    def __init__(self) -> None:
        self.client: object | None = None
        self.load_error = ""

    def load(self) -> None:
        dependency_present = google_dependency_present()
        print(
            "Loading Google proxy backend... "
            f"dependency={'yes' if dependency_present else 'no'} "
            f"credentials={google_credential_status()}"
        )
        try:
            if not dependency_present:
                raise RuntimeError("google-cloud-translate not installed")
            from google.cloud import translate_v2 as translate

            self.client = translate.Client()
        except Exception as error:
            self.load_error = str(error)
            print(f"Google proxy backend unavailable: {error}")
            return
        print("Google proxy backend ready")

    def translate(self, source_language: str, target_language: str, text: str) -> str:
        if self.load_error:
            raise RuntimeError("Google proxy backend unavailable: " + self.load_error)
        if self.client is None:
            raise RuntimeError("Google proxy client not loaded")
        try:
            result = self.client.translate(
                text,
                target_language=target_language,
                source_language=source_language,
                format_="text",
            )
        except Exception as error:
            raise RuntimeError("Google proxy translation failed: " + str(error)) from error
        if isinstance(result, list):
            if len(result) != 1:
                raise RuntimeError("Google proxy returned unexpected translation count")
            result = result[0]
        translated = html.unescape(str(result.get("translatedText", "")))
        if not translated:
            raise RuntimeError("Google proxy returned empty translation")
        return translated


def get_thread_google() -> GoogleTranslator:
    translator = getattr(THREAD_LOCAL, "google", None)
    if translator is None:
        translator = GoogleTranslator()
        translator.load()
        THREAD_LOCAL.google = translator
    return translator


def prune_cache_locked(now: float) -> None:
    if CACHE_MAX <= 0:
        TRANSLATION_CACHE.clear()
        return
    expired_keys = [
        key
        for key, (stored_at, _) in TRANSLATION_CACHE.items()
        if now - stored_at >= CACHE_TTL_SECONDS
    ]
    for key in expired_keys:
        del TRANSLATION_CACHE[key]
    while len(TRANSLATION_CACHE) > CACHE_MAX:
        TRANSLATION_CACHE.popitem(last=False)


def build_cache_key(backend: str, source_language: str, target_language: str, text: str) -> tuple[str, str, str, str]:
    return backend, source_language, target_language, text


def begin_request(key: tuple[str, str, str, str]) -> tuple[object | None, InFlightTranslation | None, bool]:
    cached_result: object | None = None
    in_flight: InFlightTranslation | None = None
    is_leader = False
    with TRANSLATION_CACHE_LOCK:
        prune_cache_locked(time.time())
        cached = TRANSLATION_CACHE.get(key)
        if cached is not None:
            _, cached_result = cached
            TRANSLATION_CACHE.move_to_end(key)
        if cached_result is None:
            in_flight = IN_FLIGHT_TRANSLATIONS.get(key)
            if in_flight is None:
                in_flight = InFlightTranslation()
                IN_FLIGHT_TRANSLATIONS[key] = in_flight
                is_leader = True
    return cached_result, in_flight, is_leader


def finish_request(key: tuple[str, str, str, str], in_flight: InFlightTranslation, translated_text: str) -> None:
    with TRANSLATION_CACHE_LOCK:
        TRANSLATION_CACHE[key] = (time.time(), translated_text)
        TRANSLATION_CACHE.move_to_end(key)
        prune_cache_locked(time.time())
        IN_FLIGHT_TRANSLATIONS.pop(key, None)
        in_flight.result = translated_text
        in_flight.error = ""
        in_flight.event.set()


def fail_request(key: tuple[str, str, str, str], in_flight: InFlightTranslation, error_text: str) -> None:
    with TRANSLATION_CACHE_LOCK:
        IN_FLIGHT_TRANSLATIONS.pop(key, None)
        in_flight.error = error_text
        in_flight.event.set()


def translate_with_cache(backend: str, source_language: str, target_language: str, text: str) -> tuple[str, bool]:
    key = build_cache_key(backend, source_language, target_language, text)
    cached_result, in_flight, is_leader = begin_request(key)
    if cached_result is not None:
        log_diag("cache_hit", backend=backend, source_language=source_language, target_language=target_language)
        return str(cached_result), True
    if not is_leader:
        log_diag("dedupe_wait", backend=backend, source_language=source_language, target_language=target_language)
        in_flight.event.wait()
        if in_flight.error:
            log_diag("dedupe_error", backend=backend, source_language=source_language, target_language=target_language)
            raise RuntimeError(in_flight.error)
        log_diag("dedupe_result", backend=backend, source_language=source_language, target_language=target_language)
        return in_flight.result, False
    log_diag("cache_miss", backend=backend, source_language=source_language, target_language=target_language)
    log_diag("dedupe_leader", backend=backend, source_language=source_language, target_language=target_language)
    try:
        if backend == BACKEND_FAKE:
            translated_text = text
        elif backend == BACKEND_GOOGLE:
            translated_text = get_thread_google().translate(source_language, target_language, text)
        else:
            raise RuntimeError(f"unsupported proxy backend: {backend}")
    except Exception as error:
        error_text = str(error)
        fail_request(key, in_flight, error_text)
        raise
    finish_request(key, in_flight, translated_text)
    log_diag("cache_store", backend=backend, source_language=source_language, target_language=target_language)
    return translated_text, False


class TranslationRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        request_id = str(time.time_ns())
        if self.path != "/healthz":
            log_diag(
                "rejected",
                request_id=request_id,
                reason="unknown_endpoint",
                path=preview_text(self.path),
            )
            self.send_json(404, {"ok": False, "error": "unknown endpoint"})
            return
        if not self.authorized():
            log_diag("rejected", request_id=request_id, reason="unauthorized", path="/healthz")
            self.send_json(401, {"ok": False, "error": "unauthorized"})
            return
        with TRANSLATION_CACHE_LOCK:
            cache_size = len(TRANSLATION_CACHE)
            in_flight_count = len(IN_FLIGHT_TRANSLATIONS)
        self.send_json(
            200,
            {
                "ok": True,
                "backend": BACKEND,
                "auth_enabled": AUTH_ENABLED,
                "cache_size": cache_size,
                "in_flight_count": in_flight_count,
            },
        )

    def do_POST(self) -> None:
        request_id = str(time.time_ns())
        if self.path != "/translate":
            log_diag(
                "rejected",
                request_id=request_id,
                reason="unknown_endpoint",
                path=preview_text(self.path),
            )
            self.send_json(404, {"ok": False, "error": "unknown endpoint"})
            return

        try:
            content_length_header = self.headers.get("Content-Length")
            if content_length_header is None:
                raise ValueError("Content-Length missing")
            try:
                content_length = int(content_length_header)
            except ValueError as error:
                raise ValueError("Content-Length must be an integer") from error
            if content_length < 0:
                raise ValueError("Content-Length must be non-negative")
            if content_length > MAX_REQUEST_BYTES:
                raise ValueError("request body too large")
            if not self.authorized():
                log_diag("rejected", request_id=request_id, reason="unauthorized", bytes=content_length)
                self.send_json(401, {"ok": False, "error": "unauthorized"})
                return
            raw_body = self.rfile.read(content_length) if content_length > 0 else b""
            payload = json.loads(raw_body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            source_language = payload.get("source_language")
            target_language = payload.get("target_language")
            text = payload.get("text")
            direction = payload.get("direction")
            if (source_language is None and target_language is None) and direction is None:
                raise ValueError("source_language/target_language or direction required")
            if source_language is None and target_language is None and direction is not None:
                if direction == "en_to_ru":
                    source_language, target_language = "en", "ru"
                elif direction == "ru_to_en":
                    source_language, target_language = "ru", "en"
                else:
                    raise ValueError("unsupported direction")
            if source_language is None or target_language is None:
                raise ValueError("both source_language and target_language are required")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("text must be a non-empty string")
            source_language = normalize_language_code(source_language, "source_language")
            target_language = normalize_language_code(target_language, "target_language")
            log_diag(
                "request",
                request_id=request_id,
                source_language=source_language,
                target_language=target_language,
                preview=preview_text(text),
            )
            if source_language == target_language:
                translated_text = text
                self.send_json(
                    200,
                    {
                        "ok": True,
                        "source_language": source_language,
                        "target_language": target_language,
                        "translated_text": translated_text,
                    },
                )
                return
            translated_text, _ = translate_with_cache(BACKEND, source_language, target_language, text)
            self.send_json(
                200,
                {
                    "ok": True,
                    "source_language": source_language,
                    "target_language": target_language,
                    "translated_text": translated_text,
                },
            )
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            log_diag("error", request_id=request_id, error=preview_text(str(error)))
            self.send_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            log_diag("error", request_id=request_id, error=preview_text(str(error)))
            self.send_json(500, {"ok": False, "error": str(error)})

    def authorized(self) -> bool:
        if not AUTH_ENABLED:
            return True
        header = self.headers.get("Authorization", "")
        if not header:
            return False
        scheme, _, supplied = header.partition(" ")
        if scheme.lower() != "bearer":
            return False
        return hmac.compare_digest(supplied, TOKEN)

    def send_json(self, status_code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def serve() -> None:
    server = ThreadingHTTPServer((HOST, PORT), TranslationRequestHandler)
    print(f"FORP translation proxy listening on {HOST}:{PORT}", flush=True)
    print(f"backend={BACKEND}", flush=True)
    print(f"cache max={CACHE_MAX} ttl_seconds={CACHE_TTL_SECONDS:g}", flush=True)
    print(f"auth_enabled={'yes' if AUTH_ENABLED else 'no'}", flush=True)
    print(f"python={sys.executable}", flush=True)
    if BACKEND == BACKEND_GOOGLE:
        print(
            f"google_dependency={'yes' if google_dependency_present() else 'no'} "
            f"credentials={google_credential_status()}",
            flush=True,
        )
    if not AUTH_ENABLED:
        print("WARNING: FORP_PROXY_TOKEN is not set; proxy is unauthenticated.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("FORP translation proxy stopped.", flush=True)
    finally:
        server.server_close()


def main() -> None:
    if BACKEND == BACKEND_GOOGLE:
        google = get_thread_google()
        if google.load_error:
            raise RuntimeError(google.load_error)
    serve()


if __name__ == "__main__":
    main()
