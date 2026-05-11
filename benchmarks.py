from __future__ import annotations

from collections.abc import Iterable


BENCHMARKS = (
    "llm-dreams-benchmark",
    "pm-llm-benchmark",
    "pmllmbench-lrms-reasoning-analysis",
    "hallucin-pm-bench",
    "d-bench",
)
NO_ARGUMENT_BENCHMARKS = {"pmllmbench-lrms-reasoning-analysis"}
ALL_BENCHMARKS_TOKEN = "all"


def benchmark_choices_text() -> str:
    return ", ".join((*BENCHMARKS, ALL_BENCHMARKS_TOKEN))


def normalize_benchmark_selection(values: str | Iterable[str] | None, *, default_to_all: bool = True) -> tuple[str, ...]:
    if values is None:
        return BENCHMARKS if default_to_all else ()

    raw_values = [values] if isinstance(values, str) else list(values)
    selected: list[str] = []
    for value in raw_values:
        if value is None:
            continue
        selected.extend(part.strip() for part in str(value).split(",") if part.strip())

    if not selected:
        return BENCHMARKS if default_to_all else ()

    if ALL_BENCHMARKS_TOKEN in selected:
        return BENCHMARKS

    unknown = sorted({benchmark for benchmark in selected if benchmark not in BENCHMARKS})
    if unknown:
        raise ValueError(f"Unknown benchmark(s): {', '.join(unknown)}. Valid values: {benchmark_choices_text()}.")

    selected_set = set(selected)
    return tuple(benchmark for benchmark in BENCHMARKS if benchmark in selected_set)
