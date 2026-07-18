#!/usr/bin/env python3

import argparse
import csv
import importlib.util
import time
from dataclasses import dataclass

SENTINEL = "[#FORP{index:04d}#]"


EN_MEDIUM = [
    "medium diagnostic scene line {id} with enough words to exercise Argos during a crowded roleplay scene"
]
EN_LONG = [
    "long diagnostic roleplay line {id} describing a crowded medical exchange, timing pressure, repeated context, and translation load"
]
RU_MEDIUM = [
    "привет доктор это проверочная строка {id} для crowded scene test"
]
PATHOLOGICAL = [
    "HMM.... OKAY INTERESTING",
    "AHA! THERE WE GO!",
]


@dataclass
class BenchRow:
    backend: str
    direction: str
    size: int
    total_seconds: float
    average_seconds: float
    output_count: int
    markers_survived: str
    malformed: str
    preview: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark local translation batching options."
    )
    parser.add_argument(
        "--directions",
        default="both",
        choices=("both", "en_to_ru", "ru_to_en"),
    )
    parser.add_argument("--sizes", default="1,3,10,30")
    parser.add_argument("--beam-size", type=int, default=1)
    parser.add_argument("--csv")
    parser.add_argument("--include-pathological", action="store_true")
    parser.add_argument("--skip-joined", action="store_true")
    parser.add_argument("--skip-direct", action="store_true")
    return parser.parse_args()


def preview(text: str) -> str:
    text = text.replace("\r", " ").replace("\n", " ")
    if len(text) > 120:
        text = text[:120] + "..."
    return text.encode("unicode_escape", errors="replace").decode("ascii")


def parse_sizes(raw: str) -> list[int]:
    sizes = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            sizes.append(int(part))
    return sizes


def directions(raw: str) -> list[str]:
    if raw == "both":
        return ["en_to_ru", "ru_to_en"]
    return [raw]


def source_target(direction: str) -> tuple[str, str]:
    if direction == "en_to_ru":
        return "en", "ru"
    return "ru", "en"


def make_lines(direction: str, size: int, include_pathological: bool) -> list[str]:
    if direction == "en_to_ru":
        templates = EN_MEDIUM if size <= 10 else EN_LONG
    else:
        templates = RU_MEDIUM

    lines = [
        templates[(index - 1) % len(templates)].format(id=index)
        for index in range(1, size + 1)
    ]

    if include_pathological and direction == "en_to_ru":
        lines = PATHOLOGICAL + lines
        return lines[:size]

    return lines


def row(
    backend: str,
    direction: str,
    lines: list[str],
    total_seconds: float,
    outputs: list[str],
    markers_survived: str = "-",
) -> BenchRow:
    malformed = "no"
    if len(outputs) != len(lines):
        malformed = "yes"
    if any(not output for output in outputs):
        malformed = "yes"
    return BenchRow(
        backend=backend,
        direction=direction,
        size=len(lines),
        total_seconds=total_seconds,
        average_seconds=total_seconds / max(len(lines), 1),
        output_count=len(outputs),
        markers_survived=markers_survived,
        malformed=malformed,
        preview=preview(outputs[0]) if outputs else "",
    )


def argos_available() -> bool:
    return importlib.util.find_spec("argostranslate") is not None


def ctranslate2_available() -> bool:
    return importlib.util.find_spec("ctranslate2") is not None


def bench_argos_separate(direction: str, lines: list[str]) -> BenchRow:
    from argostranslate import translate

    source, target = source_target(direction)
    start = time.perf_counter()
    outputs = [translate.translate(line, source, target) for line in lines]
    elapsed = time.perf_counter() - start
    return row("argos_separate", direction, lines, elapsed, outputs)


def split_joined_output(output: str, size: int) -> tuple[list[str], bool]:
    outputs = []
    for index in range(1, size + 1):
        marker = SENTINEL.format(index=index)
        next_marker = (
            SENTINEL.format(index=index + 1)
            if index < size
            else None
        )
        start = output.find(marker)
        if start == -1:
            return [], False
        start += len(marker)
        end = output.find(next_marker) if next_marker else len(output)
        if end == -1:
            return [], False
        outputs.append(output[start:end].strip())
    return outputs, True


def bench_argos_joined(direction: str, lines: list[str]) -> BenchRow:
    from argostranslate import translate

    source, target = source_target(direction)
    joined = "\n".join(
        f"{SENTINEL.format(index=index)} {line}"
        for index, line in enumerate(lines, 1)
    )
    start = time.perf_counter()
    output = translate.translate(joined, source, target)
    elapsed = time.perf_counter() - start
    outputs, survived = split_joined_output(output, len(lines))
    return row(
        "argos_joined_marker",
        direction,
        lines,
        elapsed,
        outputs,
        "yes" if survived else "no",
    )


def package_for_direction(direction: str):
    from argostranslate import package

    source, target = source_target(direction)
    for pkg in package.get_installed_packages():
        if pkg.from_code == source and pkg.to_code == target:
            return pkg
    raise RuntimeError(f"missing Argos package {direction}")


def bench_ctranslate2_direct(
    direction: str,
    lines: list[str],
    beam_size: int,
) -> BenchRow:
    import ctranslate2
    from argostranslate import settings

    pkg = package_for_direction(direction)
    translator = ctranslate2.Translator(
        str(pkg.package_path / "model"),
        device=settings.device,
        inter_threads=settings.inter_threads,
        intra_threads=settings.intra_threads,
        compute_type=settings.compute_type,
    )
    tokenized = [pkg.tokenizer.encode(line) for line in lines]
    start = time.perf_counter()
    batches = translator.translate_batch(
        tokenized,
        replace_unknowns=True,
        max_batch_size=settings.batch_size,
        batch_type="tokens",
        beam_size=beam_size,
        num_hypotheses=1,
        length_penalty=0.2,
    )
    elapsed = time.perf_counter() - start
    outputs = [
        pkg.tokenizer.decode(batch.hypotheses[0]).lstrip(" ")
        for batch in batches
    ]
    return row("ctranslate2_direct_batch", direction, lines, elapsed, outputs)


def write_csv(path: str, rows: list[BenchRow]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(BenchRow.__annotations__.keys()),
        )
        writer.writeheader()
        for item in rows:
            writer.writerow(item.__dict__)


def print_row(item: BenchRow) -> None:
    print(
        f"{item.backend} direction={item.direction} size={item.size} "
        f"total={item.total_seconds:.3f}s avg={item.average_seconds:.3f}s "
        f"outputs={item.output_count} markers={item.markers_survived} "
        f"malformed={item.malformed} preview={item.preview}"
    )


def main() -> None:
    args = parse_args()
    sizes = parse_sizes(args.sizes)
    results: list[BenchRow] = []

    print(f"argostranslate={'yes' if argos_available() else 'no'}")
    print(f"ctranslate2={'yes' if ctranslate2_available() else 'no'}")

    for direction in directions(args.directions):
        for size in sizes:
            lines = make_lines(direction, size, args.include_pathological)

            if argos_available():
                item = bench_argos_separate(direction, lines)
                results.append(item)
                print_row(item)

                if not args.skip_joined:
                    item = bench_argos_joined(direction, lines)
                    results.append(item)
                    print_row(item)

            if ctranslate2_available() and not args.skip_direct:
                item = bench_ctranslate2_direct(direction, lines, args.beam_size)
                results.append(item)
                print_row(item)

    if args.csv:
        write_csv(args.csv, results)
        print(f"Wrote CSV: {args.csv}")


if __name__ == "__main__":
    main()
