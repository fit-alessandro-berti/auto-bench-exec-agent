#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
BENCHMARKS = (
    "llm-dreams-benchmark",
    "pm-llm-benchmark",
    "pmllmbench-lrms-reasoning-analysis",
    "hallucin-pm-bench",
    "d-bench",
)
NO_ARGUMENT_BENCHMARKS = {"pmllmbench-lrms-reasoning-analysis"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute all benchmark CLIs for a target model.")
    parser.add_argument("model_name", help="Model alias to benchmark.")
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
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing them.")
    return parser


def resolve_benchmark_cli(benchmark_name: str) -> Path:
    for root in (REPO_ROOT / benchmark_name, REPO_ROOT.parent / benchmark_name):
        candidate = root / "cli_execute.py"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Could not find {benchmark_name}/cli_execute.py in current or parent directory.")


def run_app_git_preflight(dry_run: bool) -> None:
    if not (REPO_ROOT / ".git").exists():
        return
    for command in (["git", "reset", "--hard", "HEAD"], ["git", "clean", "-x", "-f"], ["git", "pull"]):
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


def build_child_env() -> dict[str, str]:
    env = os.environ.copy()
    python_path_entries = [str(REPO_ROOT)]
    if env.get("PYTHONPATH"):
        python_path_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path_entries)
    env["AUTO_BENCH_THREAD_GUARD"] = "1"
    try:
        env["AUTO_BENCH_MAX_WORKERS"] = str(max(int(env.get("AUTO_BENCH_MAX_WORKERS", "60")), 60))
    except ValueError:
        env["AUTO_BENCH_MAX_WORKERS"] = "60"
    env.setdefault("AUTO_BENCH_FORCE_CONFIGURED_WORKERS", "1")
    env.setdefault("EVALUATION_MAX_WORKERS", env["AUTO_BENCH_MAX_WORKERS"])
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    env.setdefault("MKL_NUM_THREADS", "1")
    env.setdefault("NUMEXPR_NUM_THREADS", "1")
    env.setdefault("VECLIB_MAXIMUM_THREADS", "1")
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    return env


def main() -> None:
    parser = build_parser()
    args, unknown = parser.parse_known_args()
    if unknown:
        parser.error(f"Unknown arguments: {' '.join(unknown)}")

    run_app_git_preflight(args.dry_run)

    child_env = build_child_env()
    print(f"Thread workers: {child_env['AUTO_BENCH_MAX_WORKERS']}")
    forwarded_args = sys.argv[1:]
    for benchmark in BENCHMARKS:
        script_path = resolve_benchmark_cli(benchmark)
        child_args = [] if benchmark in NO_ARGUMENT_BENCHMARKS else forwarded_args
        command = [args.python, str(script_path)] + child_args
        run_subprocess(command, cwd=script_path.parent, dry_run=args.dry_run, env=child_env)
        if benchmark == "pm-llm-benchmark":
            prepare_lrm_reasoning_inputs(args.python, args.dry_run, child_env)


if __name__ == "__main__":
    main()
