#!/usr/bin/env python3

import argparse
import csv
import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

MAGIC = b"TLJ1"
RESERVED = b"\x00\x00\x00"
HEADER = struct.Struct("<4sB3sI")
MAX_PAYLOAD = 8192
PREVIEW_LIMIT = 80

OUTCOME_SUCCESS = "success"
OUTCOME_PROTOCOL_ERROR_STATUS = "protocol_error_status"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_CONNECTION_ERROR = "connection_error"
OUTCOME_BAD_HEADER = "bad_header"
OUTCOME_BAD_UTF8 = "bad_utf8"


@dataclass
class RequestResult:
    request_id: int
    operation: int
    input_bytes: int
    input_preview: str
    start_timestamp: str
    connect_elapsed: float
    send_elapsed: float
    total_elapsed: float
    response_status: int | None
    response_bytes: int
    response_preview: str
    outcome: str
    exception: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synthetic TLJ1 TCP translation worker load tester."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=33742)
    parser.add_argument("--operation", type=int, default=3, choices=(1, 2, 3, 4))
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=1.2)
    parser.add_argument("--message")
    parser.add_argument("--message-file")
    parser.add_argument("--include-pathological", action="store_true")
    parser.add_argument("--delay-between-starts", type=float, default=0.0)
    parser.add_argument("--csv")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def preview_text(text: str) -> str:
    text = text.replace("\r", " ").replace("\n", " ")
    if len(text) > PREVIEW_LIMIT:
        text = text[:PREVIEW_LIMIT] + "..."
    return text.encode("unicode_escape", errors="replace").decode("ascii")


def decode_preview(payload: bytes, strict: bool) -> tuple[str, bool]:
    try:
        return preview_text(payload.decode("utf-8")), True
    except UnicodeDecodeError:
        if strict:
            return "", False
        return preview_text(payload.decode("utf-8", errors="replace")), True


def receive_exact(sock: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise ConnectionError("connection closed before frame completed")
        data.extend(chunk)
    return bytes(data)


def request_message(messages: list[str], request_id: int) -> str:
    template = messages[(request_id - 1) % len(messages)]
    try:
        return template.format(id=request_id, index=request_id - 1)
    except (IndexError, KeyError, ValueError):
        return template


def load_messages(args: argparse.Namespace) -> list[str]:
    messages: list[str] = []
    if args.message_file:
        path = Path(args.message_file)
        messages.extend(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    if args.message:
        messages.append(args.message)
    if args.include_pathological:
        messages.extend(
            [
                "HMM.... OKAY INTERESTING",
                "AHA! THERE WE GO!",
            ]
        )
    if not messages:
        messages.append("hello doctor {id}")
    return messages


def build_request(operation: int, payload: bytes) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload exceeds {MAX_PAYLOAD} bytes")
    return HEADER.pack(MAGIC, operation, RESERVED, len(payload)) + payload


def run_request(
    request_id: int,
    args: argparse.Namespace,
    messages: list[str],
) -> RequestResult:
    message = request_message(messages, request_id)
    payload = message.encode("utf-8")
    input_preview = preview_text(message)
    start_timestamp = datetime.now().isoformat(timespec="milliseconds")
    started = time.perf_counter()
    connect_elapsed = 0.0
    send_elapsed = 0.0
    response_status: int | None = None
    response_bytes = 0
    response_preview = ""
    outcome = OUTCOME_CONNECTION_ERROR
    exception = ""

    try:
        request_frame = build_request(args.operation, payload)

        connect_started = time.perf_counter()
        with socket.create_connection(
            (args.host, args.port),
            timeout=args.timeout,
        ) as sock:
            connect_elapsed = time.perf_counter() - connect_started
            sock.settimeout(args.timeout)

            send_started = time.perf_counter()
            sock.sendall(request_frame)
            send_elapsed = time.perf_counter() - send_started

            response_header = receive_exact(sock, HEADER.size)
            magic, response_status, reserved, response_length = HEADER.unpack(
                response_header
            )
            if magic != MAGIC or reserved != RESERVED or response_length > MAX_PAYLOAD:
                outcome = OUTCOME_BAD_HEADER
                raise ValueError("bad TLJ1 response header")

            response_payload = receive_exact(sock, response_length)
            response_bytes = len(response_payload)

            if response_status != 0:
                response_preview, _ = decode_preview(response_payload, strict=False)
                outcome = OUTCOME_PROTOCOL_ERROR_STATUS
            else:
                response_preview, ok = decode_preview(response_payload, strict=True)
                outcome = OUTCOME_SUCCESS if ok else OUTCOME_BAD_UTF8

    except socket.timeout as error:
        outcome = OUTCOME_TIMEOUT
        exception = str(error)
    except (ConnectionError, OSError) as error:
        outcome = OUTCOME_CONNECTION_ERROR
        exception = str(error)
    except UnicodeDecodeError as error:
        outcome = OUTCOME_BAD_UTF8
        exception = str(error)
    except ValueError as error:
        if outcome == OUTCOME_CONNECTION_ERROR:
            outcome = OUTCOME_BAD_HEADER
        exception = str(error)

    return RequestResult(
        request_id=request_id,
        operation=args.operation,
        input_bytes=len(payload),
        input_preview=input_preview,
        start_timestamp=start_timestamp,
        connect_elapsed=connect_elapsed,
        send_elapsed=send_elapsed,
        total_elapsed=time.perf_counter() - started,
        response_status=response_status,
        response_bytes=response_bytes,
        response_preview=response_preview,
        outcome=outcome,
        exception=exception,
    )


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * fraction))
    return ordered[index]


def write_csv(path: str, results: list[RequestResult]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "request_id",
                "operation",
                "input_bytes",
                "input_preview",
                "start_timestamp",
                "connect_elapsed",
                "send_elapsed",
                "total_elapsed",
                "response_status",
                "response_bytes",
                "response_preview",
                "outcome",
                "exception",
            ],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(result.__dict__)


def print_result(result: RequestResult) -> None:
    print(
        "id={id} outcome={outcome} op={op} input_bytes={input_bytes} "
        "status={status} response_bytes={response_bytes} total={total:.3f}s "
        "preview={preview} error={error}".format(
            id=result.request_id,
            outcome=result.outcome,
            op=result.operation,
            input_bytes=result.input_bytes,
            status="-" if result.response_status is None else result.response_status,
            response_bytes=result.response_bytes,
            total=result.total_elapsed,
            preview=result.input_preview,
            error=result.exception,
        )
    )


def print_summary(results: list[RequestResult], started: float, finished: float) -> None:
    total = len(results)
    success_count = sum(1 for result in results if result.outcome == OUTCOME_SUCCESS)
    failure_count = total - success_count
    timeout_count = sum(1 for result in results if result.outcome == OUTCOME_TIMEOUT)
    protocol_count = sum(
        1 for result in results if result.outcome == OUTCOME_PROTOCOL_ERROR_STATUS
    )
    latencies = [result.total_elapsed for result in results]
    duration = max(finished - started, 0.000001)
    over_1s = sum(1 for latency in latencies if latency > 1.0)
    over_4s = sum(1 for latency in latencies if latency > 4.0)

    print()
    print("Summary")
    print(f"total requests: {total}")
    print(f"success count: {success_count}")
    print(f"failure count: {failure_count}")
    print(f"timeout count: {timeout_count}")
    print(f"protocol error count: {protocol_count}")
    print(f"p50 latency: {percentile(latencies, 0.50):.3f}s")
    print(f"p95 latency: {percentile(latencies, 0.95):.3f}s")
    print(f"max latency: {max(latencies, default=0.0):.3f}s")
    print(f"responses over 1s: {over_1s}")
    print(f"responses over 4s: {over_4s}")
    print(f"throughput requests/sec: {total / duration:.3f}")


def main() -> None:
    args = parse_args()
    if args.count < 1:
        raise SystemExit("--count must be at least 1")
    if args.concurrency < 1:
        raise SystemExit("--concurrency must be at least 1")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be greater than zero")
    if args.delay_between_starts < 0:
        raise SystemExit("--delay-between-starts must not be negative")

    messages = load_messages(args)
    results: list[RequestResult] = []
    started = time.perf_counter()

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = []
        for request_id in range(1, args.count + 1):
            futures.append(executor.submit(run_request, request_id, args, messages))
            if args.delay_between_starts > 0:
                time.sleep(args.delay_between_starts)

        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            if args.verbose:
                print_result(result)

    finished = time.perf_counter()
    results.sort(key=lambda result: result.request_id)

    if args.csv:
        write_csv(args.csv, results)
        print(f"Wrote CSV: {args.csv}")

    print_summary(results, started, finished)


if __name__ == "__main__":
    main()
