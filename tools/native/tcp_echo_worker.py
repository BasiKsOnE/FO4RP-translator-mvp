#!/usr/bin/env python3

import socket
import struct
import time

HOST = "127.0.0.1"
PORT = 33742
MAGIC = b"TLJ1"
RESERVED = b"\x00\x00\x00"
OPERATION_ECHO = 1
OPERATION_FAKE_TRANSLATE = 2
STATUS_SUCCESS = 0
STATUS_ERROR = 1
MAX_PAYLOAD = 8192
FAKE_TRANSLATION_PREFIX = "[PY FAKE] "
CONNECTION_TIMEOUT_SECONDS = 1.0
ACCEPT_TIMEOUT_SECONDS = 0.25
HEADER = struct.Struct("<4sB3sI")


class ProtocolError(Exception):
    pass


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
    connection: socket.socket, address: tuple[str, int]
) -> None:
    started = time.perf_counter()

    try:
        header = receive_exact(connection, HEADER.size)
        magic, operation, reserved, payload_length = HEADER.unpack(header)

        if magic != MAGIC:
            raise ProtocolError("invalid magic")
        if operation != OPERATION_ECHO and operation != OPERATION_FAKE_TRANSLATE:
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

        if operation == OPERATION_ECHO:
            response_payload = payload
            action = "Echoed"
        else:
            response_payload = (
                FAKE_TRANSLATION_PREFIX + payload_text
            ).encode("utf-8")
            if len(response_payload) > MAX_PAYLOAD:
                raise ProtocolError("response payload too large")
            action = "Fake-translated"

        send_response(connection, STATUS_SUCCESS, response_payload)
        elapsed = time.perf_counter() - started
        print(
            f"{action} {len(payload)} bytes for "
            f"{address[0]}:{address[1]} in {elapsed:.3f}s"
        )
    except (ProtocolError, OSError) as error:
        diagnostic = str(error).encode("utf-8", errors="replace")[:MAX_PAYLOAD]
        try:
            send_response(connection, STATUS_ERROR, diagnostic)
        except OSError:
            pass

        elapsed = time.perf_counter() - started
        print(
            f"Rejected request from {address[0]}:{address[1]} "
            f"in {elapsed:.3f}s: {error}"
        )


def serve() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((HOST, PORT))
        listener.listen(1)
        listener.settimeout(ACCEPT_TIMEOUT_SECONDS)

        print(f"TLJ TCP echo worker listening on {HOST}:{PORT}")

        while True:
            try:
                connection, address = listener.accept()
            except socket.timeout:
                continue

            with connection:
                if address[0] != HOST:
                    print(f"Rejected non-loopback connection from {address[0]}")
                    continue

                connection.settimeout(CONNECTION_TIMEOUT_SECONDS)
                handle_connection(connection, address)


def main() -> None:
    try:
        serve()
    except KeyboardInterrupt:
        print("TLJ TCP echo worker stopped.")


if __name__ == "__main__":
    main()
