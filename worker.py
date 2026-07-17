#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks import normalize_benchmark_selection
from process_cleanup import JOB_ID_ENV, cleanup_spawned_processes


APP_ROOT = Path(__file__).resolve().parent
STATE_DIR = APP_ROOT / "state"
LOG_DIR = APP_ROOT / "logs"
STATUS_PATH = STATE_DIR / "status.json"
LOCK_PATH = STATE_DIR / "benchmark.lock"
LOG_PATH = LOG_DIR / "benchmark.log"


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_status() -> dict[str, Any]:
    try:
        with STATUS_PATH.open("r", encoding="utf-8") as handler:
            status = json.load(handler)
        if isinstance(status, dict):
            return status
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {"state": "idle"}


def write_status(status: dict[str, Any]) -> None:
    ensure_dirs()
    tmp_path = STATUS_PATH.with_name(f"{STATUS_PATH.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handler:
        json.dump(status, handler, indent=2)
        handler.write("\n")
    tmp_path.replace(STATUS_PATH)


def release_lock() -> None:
    LOCK_PATH.unlink(missing_ok=True)


def optional_arg(command: list[str], flag: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    command.extend([flag, str(value)])


def build_command(config: dict[str, Any]) -> list[str]:
    command = [sys.executable, str(APP_ROOT / "cli_execute.py"), config["model_name"]]
    if "benchmarks" in config:
        selected_benchmarks = normalize_benchmark_selection(config.get("benchmarks"), default_to_all=False)
        if not selected_benchmarks:
            raise ValueError("At least one benchmark must be selected.")
        for benchmark in selected_benchmarks:
            optional_arg(command, "--benchmark", benchmark)
    optional_arg(command, "--provider", config.get("provider"))
    optional_arg(command, "--base-model", config.get("base_model"))
    optional_arg(command, "--alias", config.get("alias"))
    optional_arg(command, "--api-url", config.get("api_url"))
    optional_arg(command, "--api-key-env", config.get("api_key_env"))
    optional_arg(command, "--api-key-file", config.get("api_key_file"))
    optional_arg(command, "--reasoning-effort", config.get("reasoning_effort"))
    if config.get("reasoning_enabled"):
        command.append("--reasoning-enabled")
    optional_arg(command, "--thinking-tokens", config.get("thinking_tokens"))
    optional_arg(command, "--temperature", config.get("temperature"))
    optional_arg(command, "--max-tokens", config.get("max_tokens"))
    optional_arg(command, "--system-prompt", config.get("system_prompt"))
    optional_arg(command, "--add-prompt", config.get("add_prompt"))
    optional_arg(command, "--payload-json", config.get("payload_json"))
    optional_arg(command, "--tools-json", config.get("tools_json"))
    optional_arg(command, "--config-json", config.get("config_json"))
    optional_arg(command, "--config-file", config.get("config_file"))
    optional_arg(command, "--max-worker-threads", config.get("max_worker_threads"))
    if config.get("disable_git_clean"):
        command.append("--disable-git-clean")
    return command


def build_child_env(config: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    python_path_entries = [str(APP_ROOT)]
    if env.get("PYTHONPATH"):
        python_path_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path_entries)
    env["AUTO_BENCH_THREAD_GUARD"] = "1"
    default_workers = parse_worker_count(env.get("AUTO_BENCH_MAX_WORKERS") or "60", 60)
    requested_workers = parse_worker_count(config.get("max_worker_threads") or default_workers, default_workers)
    env["AUTO_BENCH_MAX_WORKERS"] = str(requested_workers)
    env["EVALUATION_MAX_WORKERS"] = env["AUTO_BENCH_MAX_WORKERS"]
    env["OMP_NUM_THREADS"] = "1"
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"
    env["NUMEXPR_NUM_THREADS"] = "1"
    env["VECLIB_MAXIMUM_THREADS"] = "1"
    env["TOKENIZERS_PARALLELISM"] = "false"
    return env


def popen_detached_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def parse_worker_count(value: Any, fallback: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return fallback


def run_benchmarks(config: dict[str, Any]) -> int:
    command = build_command(config)
    child_env = build_child_env(config)
    initial_status = read_status()
    started_at = initial_status.get("started_at") or utc_now()
    job_id = initial_status.get("job_id") or os.environ.get(JOB_ID_ENV)
    with LOG_PATH.open("a", encoding="utf-8") as log_handler:
        log_handler.write(f"[{utc_now()}] worker pid={os.getpid()}\n")
        log_handler.write(f"[{utc_now()}] thread workers={child_env.get('AUTO_BENCH_MAX_WORKERS')}\n")
        log_handler.write(f"[{utc_now()}] $ {' '.join(command)}\n")
        process = subprocess.Popen(
            command,
            cwd=str(APP_ROOT),
            stdout=log_handler,
            stderr=subprocess.STDOUT,
            text=True,
            env=child_env,
            **popen_detached_kwargs(),
        )
        write_status(
            {
                "state": "running",
                "job_id": job_id,
                "worker_pid": os.getpid(),
                "pid": process.pid,
                "command": command,
                "config": config,
                "started_at": started_at,
                "log_path": str(LOG_PATH),
            }
        )
        returncode = process.wait()
        latest_status = read_status()
        was_stopped = latest_status.get("state") in {"stopping", "stopped"}
        cleanup = cleanup_spawned_processes(
            {**latest_status, "job_id": job_id, "worker_pid": os.getpid(), "pid": process.pid},
            exclude_pids={os.getpid()},
            trust_recorded_pids=True,
        )
        write_status(
            {
                "state": "stopped" if was_stopped else "completed" if returncode == 0 else "failed",
                "job_id": job_id,
                "worker_pid": os.getpid(),
                "pid": process.pid,
                "command": command,
                "config": config,
                "started_at": started_at,
                "finished_at": utc_now(),
                "returncode": None if was_stopped else returncode,
                "log_path": str(LOG_PATH),
                "cleanup_at": utc_now(),
                **cleanup,
            }
        )
        if was_stopped:
            log_handler.write(f"[{utc_now()}] stopped by request\n")
        else:
            log_handler.write(f"[{utc_now()}] finished with return code {returncode}\n")
        log_handler.write(
            f"[{utc_now()}] spawned-thread cleanup terminated={len(cleanup['terminated_pids'])} "
            f"remaining={len(cleanup['remaining_pids'])}\n"
        )
    return returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Background worker for the Streamlit benchmark executor.")
    parser.add_argument("--config-file", required=True, help="JSON file containing the submitted benchmark config.")
    return parser.parse_args()


def main() -> int:
    ensure_dirs()
    args = parse_args()
    command: list[str] | None = None
    config: dict[str, Any] = {}

    def terminate_worker(signum: int, _frame: Any) -> None:
        signal.signal(signum, signal.SIG_IGN)
        raise SystemExit(128 + signum)

    for signal_name in ("SIGTERM", "SIGINT"):
        if hasattr(signal, signal_name):
            signal.signal(getattr(signal, signal_name), terminate_worker)

    try:
        with open(args.config_file, "r", encoding="utf-8") as handler:
            loaded = json.load(handler)
        if not isinstance(loaded, dict):
            raise ValueError("config-file must contain a JSON object.")
        config = loaded
        command = build_command(config)
        return run_benchmarks(config)
    except Exception as exc:
        status = read_status()
        write_status(
            {
                "state": "failed",
                "job_id": status.get("job_id") or os.environ.get(JOB_ID_ENV),
                "worker_pid": os.getpid(),
                "pid": status.get("pid"),
                "command": command,
                "config": config or status.get("config"),
                "started_at": status.get("started_at"),
                "finished_at": utc_now(),
                "returncode": None,
                "error": str(exc),
                "log_path": str(LOG_PATH),
            }
        )
        with LOG_PATH.open("a", encoding="utf-8") as log_handler:
            log_handler.write(f"[{utc_now()}] worker failed: {exc}\n")
        return 1
    finally:
        status = read_status()
        cleanup = cleanup_spawned_processes(
            status,
            exclude_pids={os.getpid()},
            trust_recorded_pids=True,
        )
        if status.get("state") in {"starting", "running", "stopping"}:
            was_stopping = status.get("state") == "stopping"
            write_status(
                {
                    **status,
                    "state": "stopped" if was_stopping else "failed",
                    "finished_at": utc_now(),
                    "returncode": None,
                    "error": status.get("error") or (None if was_stopping else "The benchmark worker was terminated."),
                    "cleanup_at": utc_now(),
                    **cleanup,
                }
            )
        elif not status.get("cleanup_at"):
            write_status({**status, "cleanup_at": utc_now(), **cleanup})
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
