#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
    tmp_path = STATUS_PATH.with_suffix(".json.tmp")
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
    return command


def build_child_env(config: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    python_path_entries = [str(APP_ROOT)]
    if env.get("PYTHONPATH"):
        python_path_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path_entries)
    env["AUTO_BENCH_THREAD_GUARD"] = "1"
    env["AUTO_BENCH_MAX_WORKERS"] = str(config.get("max_worker_threads") or env.get("AUTO_BENCH_MAX_WORKERS") or "8")
    env.setdefault("EVALUATION_MAX_WORKERS", env["AUTO_BENCH_MAX_WORKERS"])
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    env.setdefault("MKL_NUM_THREADS", "1")
    env.setdefault("NUMEXPR_NUM_THREADS", "1")
    env.setdefault("VECLIB_MAXIMUM_THREADS", "1")
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    return env


def popen_detached_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def run_benchmarks(config: dict[str, Any]) -> int:
    command = build_command(config)
    child_env = build_child_env(config)
    started_at = read_status().get("started_at") or utc_now()
    with LOG_PATH.open("a", encoding="utf-8") as log_handler:
        log_handler.write(f"[{utc_now()}] worker pid={os.getpid()}\n")
        log_handler.write(f"[{utc_now()}] thread cap={child_env.get('AUTO_BENCH_MAX_WORKERS')}\n")
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
                "worker_pid": os.getpid(),
                "pid": process.pid,
                "command": command,
                "config": config,
                "started_at": started_at,
                "log_path": str(LOG_PATH),
            }
        )
        returncode = process.wait()
        write_status(
            {
                "state": "completed" if returncode == 0 else "failed",
                "worker_pid": os.getpid(),
                "pid": process.pid,
                "command": command,
                "config": config,
                "started_at": started_at,
                "finished_at": utc_now(),
                "returncode": returncode,
                "log_path": str(LOG_PATH),
            }
        )
        log_handler.write(f"[{utc_now()}] finished with return code {returncode}\n")
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
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
