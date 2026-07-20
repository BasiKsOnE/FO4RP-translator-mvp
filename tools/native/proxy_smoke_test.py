#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
import urllib.error
import urllib.request


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[index]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test the shared FORP translation proxy")
    parser.add_argument("--url", default="http://127.0.0.1:8787/translate")
    parser.add_argument("--token")
    parser.add_argument("--source-language")
    parser.add_argument("--target-language")
    parser.add_argument("--direction", choices=("en_to_ru", "ru_to_en"))
    parser.add_argument("--message", default="hello doctor")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def build_request_payload(args: argparse.Namespace, request_id: int) -> dict[str, object]:
    source_language = args.source_language
    target_language = args.target_language
    if args.direction and args.source_language is None and args.target_language is None:
        if args.direction == "en_to_ru":
            source_language, target_language = "en", "ru"
        else:
            source_language, target_language = "ru", "en"
    if source_language is None:
        source_language = "en"
    if target_language is None:
        target_language = "ru"
    message = args.message.replace("{id}", str(request_id))
    return {
        "source_language": source_language,
        "target_language": target_language,
        "text": message,
    }


def run_single_request(request_id: int, args: argparse.Namespace) -> dict[str, object]:
    payload = build_request_payload(args, request_id)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        args.url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    if args.token:
        request.add_header("Authorization", f"Bearer {args.token}")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
            response_payload = json.loads(raw_body)
            status = "ok" if isinstance(response_payload, dict) and response_payload.get("ok") else "error"
            success = status == "ok"
    except urllib.error.HTTPError as error:
        success = False
        status = f"http_error_{error.code}"
    except urllib.error.URLError as error:
        success = False
        status = "timeout" if isinstance(error.reason, TimeoutError) else "network_error"
    except Exception:
        success = False
        status = "exception"
    elapsed = time.perf_counter() - started
    result = {
        "id": request_id,
        "success": success,
        "status": status,
        "latency": elapsed,
    }
    if args.verbose:
        print(
            f"[{request_id}] success={success} status={status} latency={elapsed:.3f}s payload={payload}",
            flush=True,
        )
    return result


def main() -> int:
    args = parse_args()
    count = max(1, args.count)
    concurrency = max(1, min(args.concurrency, count))
    results: list[dict[str, object]] = []
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(run_single_request, index, args) for index in range(count)]
        results = [future.result() for future in futures]
    wall_elapsed = time.perf_counter() - started
    latencies = [float(item["latency"]) for item in results if item["success"]]
    successes = sum(1 for item in results if item["success"])
    failures = len(results) - successes
    timeouts = sum(1 for item in results if item["status"] == "timeout")
    print("Summary", flush=True)
    print(f"total={len(results)}", flush=True)
    print(f"success={successes}", flush=True)
    print(f"failure={failures}", flush=True)
    print(f"timeout={timeouts}", flush=True)
    print(f"p50={percentile(latencies, 50):.3f}s", flush=True)
    print(f"p95={percentile(latencies, 95):.3f}s", flush=True)
    print(f"max={max(latencies) if latencies else 0.0:.3f}s", flush=True)
    if wall_elapsed > 0.0 and successes:
        throughput = successes / wall_elapsed
        print(f"throughput={throughput:.3f} req/s", flush=True)
    else:
        print("throughput=0.000 req/s", flush=True)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
