#!/usr/bin/env python3

import html
import importlib.util
import socket
import struct
import os
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime

HOST = "127.0.0.1"
PORT = 33742
MAGIC = b"TLJ1"
RESERVED = b"\x00\x00\x00"
OPERATION_ECHO = 1
OPERATION_FAKE_TRANSLATE = 2
OPERATION_ARGOS_EN_RU = 3
OPERATION_ARGOS_RU_EN = 4
STATUS_SUCCESS = 0
STATUS_ERROR = 1
MAX_PAYLOAD = 8192
FAKE_TRANSLATION_PREFIX = "[PY FAKE] "
CONNECTION_TIMEOUT_SECONDS = 1.0
ACCEPT_TIMEOUT_SECONDS = 0.25
DEFAULT_LISTEN_BACKLOG = 32
DEFAULT_MAX_WORKERS = 4
DEFAULT_MAX_PENDING_REQUESTS = 16
HEADER = struct.Struct("<4sB3sI")
PREVIEW_LIMIT = 80
NEXT_REQUEST_ID = 0
BACKEND_ARGOS = "argos"
BACKEND_GOOGLE = "google"
BACKEND_FAKE = "fake"
BACKEND_ENV_NAME = "FORP_TRANSLATION_BACKEND"
BACKEND_ENV_FALLBACK_NAME = "TLJ_TRANSLATION_BACKEND"
GOOGLE_VERIFY_API_ENV_NAME = "FORP_GOOGLE_VERIFY_API"
GOOGLE_VERIFY_API_ENV_FALLBACK_NAME = "TLJ_GOOGLE_VERIFY_API"
GOOGLE_WARMUP_ENV_NAME = "FORP_GOOGLE_WARMUP"
GOOGLE_WARMUP_ENV_FALLBACK_NAME = "TLJ_GOOGLE_WARMUP"
VALID_TRANSLATION_BACKENDS = (BACKEND_ARGOS, BACKEND_GOOGLE, BACKEND_FAKE)
DEFAULT_TRANSLATION_CACHE_MAX = 2048
DEFAULT_TRANSLATION_CACHE_TTL_SECONDS = 3600.0


class ProtocolError(Exception):
    pass


class InFlightTranslation:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.result = ""
        self.error = ""


def read_env_alias(primary_name: str, fallback_name: str) -> tuple[str, str] | None:
    if primary_name in os.environ:
        return primary_name, os.environ[primary_name]
    if fallback_name in os.environ:
        return fallback_name, os.environ[fallback_name]
    return None


def read_int_env(
    primary_name: str,
    fallback_name: str,
    default: int,
    minimum: int,
) -> int:
    env_value = read_env_alias(primary_name, fallback_name)
    if env_value is None:
        return default
    name, raw_value = env_value
    try:
        value = int(raw_value)
    except ValueError:
        print(f"{name} must be an integer; using {default}")
        return default
    if value < minimum:
        print(f"{name} must be at least {minimum}; using {default}")
        return default
    return value


def read_positive_int_env(primary_name: str, fallback_name: str, default: int) -> int:
    return read_int_env(primary_name, fallback_name, default, 1)


def read_nonnegative_int_env(primary_name: str, fallback_name: str, default: int) -> int:
    return read_int_env(primary_name, fallback_name, default, 0)


def read_positive_float_env(
    primary_name: str,
    fallback_name: str,
    default: float,
) -> float:
    env_value = read_env_alias(primary_name, fallback_name)
    if env_value is None:
        return default
    name, raw_value = env_value
    try:
        value = float(raw_value)
    except ValueError:
        print(f"{name} must be a number; using {default}")
        return default
    if value <= 0.0:
        print(f"{name} must be positive; using {default}")
        return default
    return value


def read_bool_env(primary_name: str, fallback_name: str, default: bool = False) -> bool:
    env_value = read_env_alias(primary_name, fallback_name)
    if env_value is None:
        return default
    return env_value[1] == "1"


def read_translation_backend_env() -> str:
    env_value = read_env_alias(BACKEND_ENV_NAME, BACKEND_ENV_FALLBACK_NAME)
    if env_value is None:
        return BACKEND_ARGOS
    name, raw_value = env_value
    backend = raw_value.strip().lower()
    if backend in VALID_TRANSLATION_BACKENDS:
        return backend
    print(
        f"{name} must be one of "
        f"{','.join(VALID_TRANSLATION_BACKENDS)}; using {BACKEND_ARGOS}"
    )
    return BACKEND_ARGOS


LISTEN_BACKLOG = read_positive_int_env(
    "FORP_TCP_BACKLOG",
    "TLJ_TCP_BACKLOG",
    DEFAULT_LISTEN_BACKLOG,
)
MAX_WORKERS = read_positive_int_env(
    "FORP_TCP_WORKERS",
    "TLJ_TCP_WORKERS",
    DEFAULT_MAX_WORKERS,
)
MAX_PENDING_REQUESTS = read_nonnegative_int_env(
    "FORP_TCP_MAX_PENDING",
    "TLJ_TCP_MAX_PENDING",
    DEFAULT_MAX_PENDING_REQUESTS,
)
REQUEST_SLOTS = threading.BoundedSemaphore(MAX_WORKERS + MAX_PENDING_REQUESTS)
TRANSLATION_BACKEND = read_translation_backend_env()
TRANSLATION_CACHE_MAX = read_nonnegative_int_env(
    "FORP_TRANSLATION_CACHE_MAX",
    "TLJ_TRANSLATION_CACHE_MAX",
    DEFAULT_TRANSLATION_CACHE_MAX,
)
TRANSLATION_CACHE_TTL_SECONDS = read_positive_float_env(
    "FORP_TRANSLATION_CACHE_TTL_SECONDS",
    "TLJ_TRANSLATION_CACHE_TTL_SECONDS",
    DEFAULT_TRANSLATION_CACHE_TTL_SECONDS,
)
GOOGLE_VERIFY_API = read_bool_env(
    GOOGLE_VERIFY_API_ENV_NAME,
    GOOGLE_VERIFY_API_ENV_FALLBACK_NAME,
)
GOOGLE_WARMUP = read_bool_env(
    GOOGLE_WARMUP_ENV_NAME,
    GOOGLE_WARMUP_ENV_FALLBACK_NAME,
)
REQUEST_COUNTS_LOCK = threading.Lock()
TRANSLATION_CACHE_LOCK = threading.Lock()
ACTIVE_REQUESTS = 0
PENDING_REQUESTS = 0
THREAD_LOCAL = threading.local()
ARGOS_STARTUP_LOAD_ERROR = ""
GOOGLE_STARTUP_LOAD_ERROR = ""
TRANSLATION_CACHE: OrderedDict[tuple[str, str, str], tuple[float, str]] = OrderedDict()
IN_FLIGHT_TRANSLATIONS: dict[tuple[str, str, str], InFlightTranslation] = {}


def next_request_id() -> int:
    global NEXT_REQUEST_ID
    NEXT_REQUEST_ID += 1
    return NEXT_REQUEST_ID


def preview_text(text: str) -> str:
    text = text.replace("\r", " ").replace("\n", " ")
    if len(text) > PREVIEW_LIMIT:
        text = text[:PREVIEW_LIMIT] + "..."
    return text.encode("unicode_escape", errors="replace").decode("ascii")


def operation_name(operation: int) -> str:
    if operation == OPERATION_ECHO:
        return "echo"
    if operation == OPERATION_FAKE_TRANSLATE:
        return "fake"
    if operation == OPERATION_ARGOS_EN_RU:
        return "translation_en_ru"
    if operation == OPERATION_ARGOS_RU_EN:
        return "translation_ru_en"
    return "unknown"


def operation_direction(operation: int) -> str:
    if operation == OPERATION_ARGOS_EN_RU:
        return "en_to_ru"
    if operation == OPERATION_ARGOS_RU_EN:
        return "ru_to_en"
    return "-"


def log_diag(request_id: int, event: str, **fields: object) -> None:
    timestamp = datetime.now().isoformat(timespec="milliseconds")
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    if details:
        print(
            f"[FORP TCP DIAG] ts={timestamp} id={request_id} "
            f"event={event} {details}",
            flush=True,
        )
    else:
        print(
            f"[FORP TCP DIAG] ts={timestamp} id={request_id} event={event}",
            flush=True,
        )


def request_counts() -> tuple[int, int]:
    with REQUEST_COUNTS_LOCK:
        return ACTIVE_REQUESTS, PENDING_REQUESTS


def increment_pending_requests() -> tuple[int, int]:
    global PENDING_REQUESTS
    with REQUEST_COUNTS_LOCK:
        PENDING_REQUESTS += 1
        return ACTIVE_REQUESTS, PENDING_REQUESTS


def decrement_pending_requests() -> tuple[int, int]:
    global PENDING_REQUESTS
    with REQUEST_COUNTS_LOCK:
        if PENDING_REQUESTS > 0:
            PENDING_REQUESTS -= 1
        return ACTIVE_REQUESTS, PENDING_REQUESTS


def worker_request_started() -> tuple[int, int]:
    global ACTIVE_REQUESTS, PENDING_REQUESTS
    with REQUEST_COUNTS_LOCK:
        if PENDING_REQUESTS > 0:
            PENDING_REQUESTS -= 1
        ACTIVE_REQUESTS += 1
        return ACTIVE_REQUESTS, PENDING_REQUESTS


def worker_request_finished() -> tuple[int, int]:
    global ACTIVE_REQUESTS
    with REQUEST_COUNTS_LOCK:
        if ACTIVE_REQUESTS > 0:
            ACTIVE_REQUESTS -= 1
        return ACTIVE_REQUESTS, PENDING_REQUESTS


def configure_argos_environment() -> None:
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")


def direction_language_codes(direction: str) -> tuple[str, str]:
    if direction == "en_to_ru":
        return "en", "ru"
    if direction == "ru_to_en":
        return "ru", "en"
    raise ProtocolError("unsupported translation direction")


def google_credential_status() -> str:
    credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_path:
        return "not_set"
    if os.path.isfile(credentials_path):
        return "file_exists"
    return "file_missing"


def google_dependency_present() -> bool:
    try:
        return importlib.util.find_spec("google.cloud.translate_v2") is not None
    except ModuleNotFoundError:
        return False


class ArgosTranslator:
    def __init__(self) -> None:
        self.translations: dict[str, object] = {}
        self.load_error = ""

    def load(self) -> None:
        configure_argos_environment()
        started = time.perf_counter()
        print(f"Python: {sys.executable}")
        print("Loading Argos backend...")

        try:
            from argostranslate import translate

            languages = {
                language.code: language
                for language in translate.get_installed_languages()
            }
            required = {
                "en_to_ru": ("en", "ru"),
                "ru_to_en": ("ru", "en"),
            }
            for direction, (source_code, target_code) in required.items():
                if source_code not in languages or target_code not in languages:
                    raise ProtocolError(f"missing Argos model {direction}")
                self.translations[direction] = languages[source_code].get_translation(
                    languages[target_code]
                )

            self.translate("en_to_ru", "hello doctor")
            self.translate("ru_to_en", "\u043f\u0440\u0438\u0432\u0435\u0442 \u0434\u043e\u043a\u0442\u043e\u0440")
        except Exception as error:
            self.load_error = str(error)
            print(f"Argos backend unavailable: {error}")
            return

        elapsed = time.perf_counter() - started
        print(f"Argos backend ready in {elapsed:.3f}s")

    def translate(self, direction: str, text: str) -> str:
        if self.load_error:
            raise ProtocolError("Argos unavailable: " + self.load_error)
        if direction not in self.translations:
            raise ProtocolError("unsupported Argos direction")

        try:
            translated = self.translations[direction].translate(text)
        except Exception as error:
            raise ProtocolError("Argos translation failed: " + str(error)) from error

        if not translated:
            raise ProtocolError("Argos returned empty translation")
        return translated


class GoogleTranslator:
    def __init__(self) -> None:
        self.client: object | None = None
        self.load_error = ""

    def load(self, verify_api: bool = False) -> None:
        started = time.perf_counter()
        dependency_present = google_dependency_present()
        print(f"Python: {sys.executable}")
        print(
            "Loading Google Translate backend... "
            f"dependency={'yes' if dependency_present else 'no'} "
            f"credentials={google_credential_status()}"
        )

        try:
            if not dependency_present:
                raise ProtocolError("google-cloud-translate not installed")

            from google.cloud import translate_v2 as translate

            self.client = translate.Client()
            if verify_api:
                self.translate("en_to_ru", "hello doctor")
                self.translate(
                    "ru_to_en",
                    "\u043f\u0440\u0438\u0432\u0435\u0442 \u0434\u043e\u043a\u0442\u043e\u0440",
                )
        except Exception as error:
            self.load_error = str(error)
            print(f"Google Translate backend unavailable: {error}")
            return

        elapsed = time.perf_counter() - started
        print(f"Google Translate backend ready in {elapsed:.3f}s")

    def translate(self, direction: str, text: str) -> str:
        if self.load_error:
            raise ProtocolError("Google Translate unavailable: " + self.load_error)
        if self.client is None:
            raise ProtocolError("Google Translate client is not loaded")

        source_code, target_code = direction_language_codes(direction)
        try:
            result = self.client.translate(
                text,
                target_language=target_code,
                source_language=source_code,
                format_="text",
            )
        except Exception as error:
            raise ProtocolError("Google translation failed: " + str(error)) from error

        if isinstance(result, list):
            if len(result) != 1:
                raise ProtocolError("Google returned unexpected translation count")
            result = result[0]

        translated = html.unescape(str(result.get("translatedText", "")))
        if not translated:
            raise ProtocolError("Google returned empty translation")
        return translated

    def warmup(self) -> None:
        print("Running Google Translate worker warmup...")
        self.translate("en_to_ru", "hello doctor")
        self.translate(
            "ru_to_en",
            "\u043f\u0440\u0438\u0432\u0435\u0442 \u0434\u043e\u043a\u0442\u043e\u0440",
        )


def fake_translate(direction: str, text: str) -> str:
    direction_language_codes(direction)
    return FAKE_TRANSLATION_PREFIX + text


def get_thread_argos() -> ArgosTranslator:
    if ARGOS_STARTUP_LOAD_ERROR:
        translator = ArgosTranslator()
        translator.load_error = ARGOS_STARTUP_LOAD_ERROR
        return translator

    translator = getattr(THREAD_LOCAL, "argos", None)
    if translator is None:
        translator = ArgosTranslator()
        translator.load()
        THREAD_LOCAL.argos = translator
    return translator


def get_thread_google() -> GoogleTranslator:
    if GOOGLE_STARTUP_LOAD_ERROR:
        translator = GoogleTranslator()
        translator.load_error = GOOGLE_STARTUP_LOAD_ERROR
        return translator

    translator = getattr(THREAD_LOCAL, "google", None)
    if translator is None:
        translator = GoogleTranslator()
        translator.load()
        if GOOGLE_WARMUP and not translator.load_error:
            translator.warmup()
        THREAD_LOCAL.google = translator
    return translator


def translate_without_cache(direction: str, text: str) -> str:
    if TRANSLATION_BACKEND == BACKEND_FAKE:
        return fake_translate(direction, text)
    if TRANSLATION_BACKEND == BACKEND_GOOGLE:
        return get_thread_google().translate(direction, text)
    return get_thread_argos().translate(direction, text)


def translation_cache_key(direction: str, text: str) -> tuple[str, str, str]:
    return TRANSLATION_BACKEND, direction, text


def prune_translation_cache_locked(now: float) -> None:
    if TRANSLATION_CACHE_MAX <= 0:
        TRANSLATION_CACHE.clear()
        return

    expired_keys = [
        key
        for key, (stored_at, _) in TRANSLATION_CACHE.items()
        if now - stored_at >= TRANSLATION_CACHE_TTL_SECONDS
    ]
    for key in expired_keys:
        del TRANSLATION_CACHE[key]

    while len(TRANSLATION_CACHE) > TRANSLATION_CACHE_MAX:
        TRANSLATION_CACHE.popitem(last=False)


def begin_translation_request(
    request_id: int,
    key: tuple[str, str, str],
) -> tuple[str | None, InFlightTranslation | None, bool]:
    backend, direction, _ = key
    cached_result: str | None = None
    in_flight: InFlightTranslation | None = None
    is_leader = False

    with TRANSLATION_CACHE_LOCK:
        if TRANSLATION_CACHE_MAX > 0:
            prune_translation_cache_locked(time.perf_counter())
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

    if cached_result is not None:
        log_diag(request_id, "cache_hit", backend=backend, direction=direction)
    elif is_leader:
        log_diag(request_id, "cache_miss", backend=backend, direction=direction)
        log_diag(request_id, "dedupe_leader", backend=backend, direction=direction)
    else:
        log_diag(request_id, "dedupe_wait", backend=backend, direction=direction)

    return cached_result, in_flight, is_leader


def store_completed_translation_locked(
    key: tuple[str, str, str],
    translated_text: str,
) -> int:
    if TRANSLATION_CACHE_MAX <= 0:
        return 0

    TRANSLATION_CACHE[key] = (time.perf_counter(), translated_text)
    TRANSLATION_CACHE.move_to_end(key)
    prune_translation_cache_locked(time.perf_counter())
    return len(TRANSLATION_CACHE)


def finish_in_flight_translation(
    key: tuple[str, str, str],
    in_flight: InFlightTranslation,
    translated_text: str,
) -> int:
    with TRANSLATION_CACHE_LOCK:
        in_flight.result = translated_text
        cache_size = store_completed_translation_locked(key, translated_text)
        IN_FLIGHT_TRANSLATIONS.pop(key, None)
        in_flight.event.set()
        return cache_size


def fail_in_flight_translation(
    key: tuple[str, str, str],
    in_flight: InFlightTranslation,
    error_text: str,
) -> None:
    with TRANSLATION_CACHE_LOCK:
        in_flight.error = error_text
        IN_FLIGHT_TRANSLATIONS.pop(key, None)
        in_flight.event.set()


def wait_for_in_flight_translation(
    request_id: int,
    key: tuple[str, str, str],
    in_flight: InFlightTranslation,
) -> str:
    backend, direction, _ = key
    in_flight.event.wait()
    if in_flight.error:
        log_diag(request_id, "dedupe_error", backend=backend, direction=direction)
        raise ProtocolError(in_flight.error)
    if not in_flight.result:
        raise ProtocolError("translation unavailable after in-flight wait")
    log_diag(request_id, "dedupe_result", backend=backend, direction=direction)
    return in_flight.result


def translate_with_configured_backend(
    request_id: int,
    direction: str,
    text: str,
) -> str:
    key = translation_cache_key(direction, text)
    cached_result, in_flight, is_leader = begin_translation_request(request_id, key)
    if cached_result is not None:
        return cached_result
    if in_flight is None:
        raise ProtocolError("translation coordination failed")
    if not is_leader:
        return wait_for_in_flight_translation(request_id, key, in_flight)

    try:
        translated_text = translate_without_cache(direction, text)
    except Exception as error:
        error_text = str(error)
        fail_in_flight_translation(key, in_flight, error_text)
        if isinstance(error, ProtocolError):
            raise
        raise ProtocolError(error_text) from error

    cache_size = finish_in_flight_translation(key, in_flight, translated_text)
    if TRANSLATION_CACHE_MAX > 0:
        backend, cache_direction, _ = key
        log_diag(
            request_id,
            "cache_store",
            backend=backend,
            direction=cache_direction,
            cache_size=cache_size,
        )
    return translated_text


def warm_worker_translator(start_event: threading.Event) -> None:
    start_event.wait()
    if TRANSLATION_BACKEND == BACKEND_ARGOS:
        get_thread_argos()
    elif TRANSLATION_BACKEND == BACKEND_GOOGLE:
        get_thread_google()


def warm_worker_pool(executor: ThreadPoolExecutor) -> None:
    if TRANSLATION_BACKEND == BACKEND_FAKE:
        return
    if TRANSLATION_BACKEND == BACKEND_ARGOS and ARGOS_STARTUP_LOAD_ERROR:
        return
    if TRANSLATION_BACKEND == BACKEND_GOOGLE and GOOGLE_STARTUP_LOAD_ERROR:
        return

    print(f"Warming {MAX_WORKERS} worker {TRANSLATION_BACKEND} translator(s)...")
    start_event = threading.Event()
    futures = [
        executor.submit(warm_worker_translator, start_event)
        for _ in range(MAX_WORKERS)
    ]
    start_event.set()
    wait(futures)
    print("Worker translator warmup complete.")


def receive_exact(connection: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = connection.recv(length - len(data))
        if not chunk:
            raise ProtocolError("connection closed before frame completed")
        data.extend(chunk)
    return bytes(data)


def send_response(connection: socket.socket, status: int, payload: bytes) -> None:
    if len(payload) > MAX_PAYLOAD:
        payload = b"response payload too large"
        status = STATUS_ERROR

    connection.sendall(HEADER.pack(MAGIC, status, RESERVED, len(payload)))
    connection.sendall(payload)


def handle_connection(
    connection: socket.socket,
    address: tuple[str, int],
    request_id: int,
    accepted_at: float,
) -> None:
    started = time.perf_counter()

    try:
        header = receive_exact(connection, HEADER.size)
        magic, operation, reserved, payload_length = HEADER.unpack(header)

        if magic != MAGIC:
            raise ProtocolError("invalid magic")
        if operation not in (
            OPERATION_ECHO,
            OPERATION_FAKE_TRANSLATE,
            OPERATION_ARGOS_EN_RU,
            OPERATION_ARGOS_RU_EN,
        ):
            raise ProtocolError("unsupported operation")
        if reserved != RESERVED:
            raise ProtocolError("reserved bytes must be zero")
        if payload_length > MAX_PAYLOAD:
            raise ProtocolError("payload too large")

        payload = receive_exact(connection, payload_length)
        try:
            payload_text = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ProtocolError("payload is not valid UTF-8") from error

        log_diag(
            request_id,
            "request",
            remote=f"{address[0]}:{address[1]}",
            op=operation,
            op_name=operation_name(operation),
            direction=operation_direction(operation),
            bytes=len(payload),
            after_accept=f"{started - accepted_at:.3f}s",
            preview=preview_text(payload_text),
        )

        if operation == OPERATION_ECHO:
            response_payload = payload
            action = "Echoed"
        elif operation == OPERATION_FAKE_TRANSLATE:
            response_payload = (
                FAKE_TRANSLATION_PREFIX + payload_text
            ).encode("utf-8")
            if len(response_payload) > MAX_PAYLOAD:
                raise ProtocolError("response payload too large")
            action = "Fake-translated"
        else:
            direction = (
                "en_to_ru"
                if operation == OPERATION_ARGOS_EN_RU
                else "ru_to_en"
            )
            translation_started = time.perf_counter()
            log_diag(
                request_id,
                "translation_start",
                direction=direction,
                backend=TRANSLATION_BACKEND,
            )
            translated_text = translate_with_configured_backend(
                request_id,
                direction,
                payload_text,
            )
            translation_elapsed = time.perf_counter() - translation_started
            response_payload = translated_text.encode("utf-8")
            if len(response_payload) > MAX_PAYLOAD:
                raise ProtocolError("response payload too large")
            log_diag(
                request_id,
                "translation_finish",
                direction=direction,
                elapsed=f"{translation_elapsed:.3f}s",
                response_bytes=len(response_payload),
                preview=preview_text(translated_text),
                backend=TRANSLATION_BACKEND,
            )
            action = f"{TRANSLATION_BACKEND}-translated {direction}"

        log_diag(
            request_id,
            "send_start",
            status=STATUS_SUCCESS,
            response_bytes=len(response_payload),
        )
        send_response(connection, STATUS_SUCCESS, response_payload)
        elapsed = time.perf_counter() - started
        log_diag(
            request_id,
            "send_success",
            status=STATUS_SUCCESS,
            total=f"{elapsed:.3f}s",
        )
        print(
            f"{action} {len(payload)} bytes for "
            f"{address[0]}:{address[1]} in {elapsed:.3f}s"
        )
    except (ProtocolError, OSError) as error:
        diagnostic = str(error).encode("utf-8", errors="replace")[:MAX_PAYLOAD]
        elapsed = time.perf_counter() - started
        log_diag(
            request_id,
            "error",
            total=f"{elapsed:.3f}s",
            error=preview_text(str(error)),
        )
        try:
            log_diag(
                request_id,
                "send_start",
                status=STATUS_ERROR,
                response_bytes=len(diagnostic),
            )
            send_response(connection, STATUS_ERROR, diagnostic)
            log_diag(request_id, "send_success", status=STATUS_ERROR)
        except OSError as send_error:
            log_diag(
                request_id,
                "send_failed",
                status=STATUS_ERROR,
                error=preview_text(str(send_error)),
            )

        print(
            f"Rejected request from {address[0]}:{address[1]} "
            f"in {elapsed:.3f}s: {error}"
        )


def reject_overload(
    connection: socket.socket,
    address: tuple[str, int],
    request_id: int,
) -> None:
    active, pending = request_counts()
    diagnostic = b"worker overloaded"
    log_diag(
        request_id,
        "overload",
        remote=f"{address[0]}:{address[1]}",
        active=active,
        pending=pending,
        max_workers=MAX_WORKERS,
        max_pending=MAX_PENDING_REQUESTS,
    )
    try:
        connection.settimeout(CONNECTION_TIMEOUT_SECONDS)
        log_diag(
            request_id,
            "send_start",
            status=STATUS_ERROR,
            response_bytes=len(diagnostic),
        )
        send_response(connection, STATUS_ERROR, diagnostic)
        log_diag(request_id, "send_success", status=STATUS_ERROR)
    except OSError as error:
        log_diag(
            request_id,
            "send_failed",
            status=STATUS_ERROR,
            error=preview_text(str(error)),
        )


def handle_connection_task(
    connection: socket.socket,
    address: tuple[str, int],
    request_id: int,
    accepted_at: float,
    submitted_at: float,
) -> None:
    worker_started = time.perf_counter()
    active, pending = worker_request_started()
    log_diag(
        request_id,
        "worker_start",
        queued_for=f"{worker_started - submitted_at:.3f}s",
        active=active,
        pending=pending,
    )
    try:
        with connection:
            connection.settimeout(CONNECTION_TIMEOUT_SECONDS)
            handle_connection(connection, address, request_id, accepted_at)
    except Exception as error:
        log_diag(
            request_id,
            "worker_crash",
            error=preview_text(str(error)),
        )
    finally:
        active, pending = worker_request_finished()
        REQUEST_SLOTS.release()
        log_diag(
            request_id,
            "worker_finish",
            total=f"{time.perf_counter() - accepted_at:.3f}s",
            active=active,
            pending=pending,
        )


def serve() -> None:
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    warm_worker_pool(executor)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((HOST, PORT))
        listener.listen(LISTEN_BACKLOG)
        listener.settimeout(ACCEPT_TIMEOUT_SECONDS)

        print(
            f"FORP TCP translation worker listening on {HOST}:{PORT} "
            f"(workers={MAX_WORKERS}, backlog={LISTEN_BACKLOG}, "
            f"max_pending={MAX_PENDING_REQUESTS})"
        )

        try:
            while True:
                try:
                    connection, address = listener.accept()
                except socket.timeout:
                    continue

                request_id = next_request_id()
                accepted_at = time.perf_counter()
                log_diag(
                    request_id,
                    "accepted",
                    remote=f"{address[0]}:{address[1]}",
                )

                if address[0] != HOST:
                    with connection:
                        log_diag(request_id, "rejected", reason="non_loopback")
                        print(f"Rejected non-loopback connection from {address[0]}")
                    continue

                if not REQUEST_SLOTS.acquire(blocking=False):
                    with connection:
                        reject_overload(connection, address, request_id)
                    continue

                submitted_at = time.perf_counter()
                active, pending = increment_pending_requests()
                log_diag(
                    request_id,
                    "submitted",
                    active=active,
                    pending=pending,
                )
                try:
                    executor.submit(
                        handle_connection_task,
                        connection,
                        address,
                        request_id,
                        accepted_at,
                        submitted_at,
                    )
                except Exception as error:
                    active, pending = decrement_pending_requests()
                    REQUEST_SLOTS.release()
                    log_diag(
                        request_id,
                        "submit_failed",
                        active=active,
                        pending=pending,
                        error=preview_text(str(error)),
                    )
                    with connection:
                        diagnostic = b"worker unavailable"
                        try:
                            connection.settimeout(CONNECTION_TIMEOUT_SECONDS)
                            log_diag(
                                request_id,
                                "send_start",
                                status=STATUS_ERROR,
                                response_bytes=len(diagnostic),
                            )
                            send_response(connection, STATUS_ERROR, diagnostic)
                            log_diag(request_id, "send_success", status=STATUS_ERROR)
                        except OSError as send_error:
                            log_diag(
                                request_id,
                                "send_failed",
                                status=STATUS_ERROR,
                                error=preview_text(str(send_error)),
                            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


def main() -> None:
    global ARGOS_STARTUP_LOAD_ERROR, GOOGLE_STARTUP_LOAD_ERROR
    try:
        print(f"Translation backend: {TRANSLATION_BACKEND}")
        print(
            f"Translation cache: max={TRANSLATION_CACHE_MAX} "
            f"ttl_seconds={TRANSLATION_CACHE_TTL_SECONDS:g}"
        )
        if TRANSLATION_BACKEND == BACKEND_ARGOS:
            argos = ArgosTranslator()
            argos.load()
            ARGOS_STARTUP_LOAD_ERROR = argos.load_error
            del argos
        elif TRANSLATION_BACKEND == BACKEND_GOOGLE:
            google = GoogleTranslator()
            google.load(verify_api=GOOGLE_VERIFY_API)
            GOOGLE_STARTUP_LOAD_ERROR = google.load_error
            del google
        else:
            print("Using fake translation backend for operations 3/4.")
        serve()
    except KeyboardInterrupt:
        print("FORP TCP translation worker stopped.")


if __name__ == "__main__":
    main()
