#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from benchmarks import NO_ARGUMENT_BENCHMARKS, benchmark_choices_text, normalize_benchmark_selection

REPO_ROOT = Path(__file__).resolve().parent
FORWARDED_VALUE_FLAGS = {
    "--provider",
    "--base-model",
    "--alias",
    "--api-url",
    "--api-key-env",
    "--api-key-file",
    "--reasoning-effort",
    "--thinking-tokens",
    "--temperature",
    "--max-tokens",
    "--system-prompt",
    "--add-prompt",
    "--payload-json",
    "--tools-json",
    "--config-json",
    "--config-file",
}
ORCHESTRATOR_VALUE_FLAGS = {"--benchmark", "--benchmarks", "--max-worker-threads", "--python"}
ORCHESTRATOR_BOOLEAN_FLAGS = {"--disable-git-clean", "--dry-run"}


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute selected benchmark CLIs for a target model.")
    parser.add_argument("model_name", help="Model alias to benchmark.")
    parser.add_argument(
        "--benchmark",
        action="append",
        metavar="NAME",
        help=f"Benchmark to execute. Repeat to select multiple. Valid values: {benchmark_choices_text()}. Defaults to all.",
    )
    parser.add_argument(
        "--benchmarks",
        metavar="NAMES",
        help=f"Comma-separated benchmarks to execute. Valid values: {benchmark_choices_text()}. Defaults to all.",
    )
    parser.add_argument("--provider", default="openrouter", help="Model provider. Defaults to openrouter.")
    parser.add_argument("--base-model", help="Underlying API model. Defaults to model_name.")
    parser.add_argument("--alias", help="Alias used inside benchmarks. Defaults to model_name.")
    parser.add_argument("--api-url", help="Override API URL.")
    parser.add_argument("--api-key-env", help="Environment variable containing the API key.")
    parser.add_argument("--api-key-file", help="Path to a file containing the API key.")
    parser.add_argument("--reasoning-effort", help="Optional reasoning effort.")
    parser.add_argument("--reasoning-enabled", action="store_true", help="Enable reasoning in payloads where supported.")
    parser.add_argument("--thinking-tokens", type=int, help="Optional Anthropic thinking token budget.")
    parser.add_argument("--temperature", type=float, help="Optional sampling temperature.")
    parser.add_argument("--max-tokens", type=int, help="Optional max token cap.")
    parser.add_argument("--system-prompt", help="Optional system prompt.")
    parser.add_argument("--add-prompt", help="Optional prompt suffix.")
    parser.add_argument("--payload-json", help="JSON object merged into payloads where supported.")
    parser.add_argument("--tools-json", help="JSON payload for the pm-llm-benchmark manual tools field.")
    parser.add_argument("--config-json", help="Extra JSON object merged into the config.")
    parser.add_argument("--config-file", help="Path to a JSON file merged into the config.")
    parser.add_argument("--python", default=sys.executable, help="Python executable used to invoke child CLIs.")
    parser.add_argument(
        "--max-worker-threads",
        type=positive_int,
        help="Maximum Python worker threads in each benchmark subprocess. Defaults to AUTO_BENCH_MAX_WORKERS or 60.",
    )
    parser.add_argument(
        "--disable-git-clean",
        action="store_true",
        help="Skip git clean during executor preflight. Disabled by default.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing them.")
    return parser


def resolve_selected_benchmarks(args: argparse.Namespace) -> tuple[str, ...]:
    values: list[str] = []
    if args.benchmarks:
        values.append(args.benchmarks)
    if args.benchmark:
        values.extend(args.benchmark)
    return normalize_benchmark_selection(values or None)


def build_forwarded_args(raw_args: list[str]) -> list[str]:
    forwarded_args: list[str] = []
    index = 0
    while index < len(raw_args):
        arg = raw_args[index]
        if arg in ORCHESTRATOR_VALUE_FLAGS:
            index += 2
            continue
        if any(arg.startswith(f"{flag}=") for flag in ORCHESTRATOR_VALUE_FLAGS):
            index += 1
            continue
        if arg in ORCHESTRATOR_BOOLEAN_FLAGS:
            index += 1
            continue
        forwarded_args.append(arg)
        if arg in FORWARDED_VALUE_FLAGS and index + 1 < len(raw_args):
            index += 1
            forwarded_args.append(raw_args[index])
        index += 1
    return forwarded_args


def resolve_benchmark_cli(benchmark_name: str) -> Path:
    for root in (REPO_ROOT / benchmark_name, REPO_ROOT.parent / benchmark_name):
        candidate = root / "cli_execute.py"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Could not find {benchmark_name}/cli_execute.py in current or parent directory.")


def run_app_git_preflight(dry_run: bool, disable_git_clean: bool = False) -> None:
    if not (REPO_ROOT / ".git").exists():
        return
    commands = [["git", "reset", "--hard", "HEAD"]]
    if disable_git_clean:
        print("# git clean disabled")
    else:
        commands.append(["git", "clean", "-x", "-f"])
    commands.append(["git", "pull"])
    for command in commands:
        print("+", " ".join(command))
        if not dry_run:
            subprocess.run(command, cwd=str(REPO_ROOT), check=True)


def run_subprocess(command: list[str], cwd: Path, dry_run: bool, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command))
    if dry_run:
        return
    subprocess.run(command, cwd=str(cwd), env=env, check=True)


def prepare_lrm_reasoning_inputs(python_executable: str, dry_run: bool, child_env: dict[str, str]) -> None:
    pm_cli_path = resolve_benchmark_cli("pm-llm-benchmark")
    reasoning_cli_path = resolve_benchmark_cli("pmllmbench-lrms-reasoning-analysis")
    pm_root = pm_cli_path.parent
    reasoning_root = reasoning_cli_path.parent
    lrm_output_path = pm_root / "utils" / "lrms_list.txt"
    lrm_target_path = reasoning_root / "lrms_list.txt"

    run_subprocess([python_executable, "utils/list_lrms.py"], cwd=pm_root, dry_run=dry_run, env=child_env)
    print("+", "cp", str(lrm_output_path), str(lrm_target_path))
    if not dry_run:
        shutil.copy2(lrm_output_path, lrm_target_path)


def build_child_env(max_worker_threads: int | None = None) -> dict[str, str]:
    env = os.environ.copy()
    python_path_entries = [str(REPO_ROOT)]
    if env.get("PYTHONPATH"):
        python_path_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path_entries)
    env["AUTO_BENCH_THREAD_GUARD"] = "1"
    try:
        default_workers = max(1, int(env.get("AUTO_BENCH_MAX_WORKERS", "60")))
    except ValueError:
        default_workers = 60
    env["AUTO_BENCH_MAX_WORKERS"] = str(max_worker_threads or default_workers)
    env["EVALUATION_MAX_WORKERS"] = env["AUTO_BENCH_MAX_WORKERS"]
    env["OMP_NUM_THREADS"] = "1"
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"
    env["NUMEXPR_NUM_THREADS"] = "1"
    env["VECLIB_MAXIMUM_THREADS"] = "1"
    env["TOKENIZERS_PARALLELISM"] = "false"
    return env


def main() -> None:
    parser = build_parser()
    args, unknown = parser.parse_known_args()
    if unknown:
        parser.error(f"Unknown arguments: {' '.join(unknown)}")
    try:
        selected_benchmarks = resolve_selected_benchmarks(args)
    except ValueError as exc:
        parser.error(str(exc))

    run_app_git_preflight(args.dry_run, args.disable_git_clean)

    child_env = build_child_env(args.max_worker_threads)
    print(f"Thread workers: {child_env['AUTO_BENCH_MAX_WORKERS']}")
    print(f"Benchmarks: {', '.join(selected_benchmarks)}")
    forwarded_args = build_forwarded_args(sys.argv[1:])
    prepare_reasoning_inputs = "pmllmbench-lrms-reasoning-analysis" in selected_benchmarks
    for benchmark in selected_benchmarks:
        script_path = resolve_benchmark_cli(benchmark)
        child_args = [] if benchmark in NO_ARGUMENT_BENCHMARKS else forwarded_args
        command = [args.python, str(script_path)] + child_args
        run_subprocess(command, cwd=script_path.parent, dry_run=args.dry_run, env=child_env)
        if benchmark == "pm-llm-benchmark" and prepare_reasoning_inputs:
            prepare_lrm_reasoning_inputs(args.python, args.dry_run, child_env)


if __name__ == "__main__":
    main()
