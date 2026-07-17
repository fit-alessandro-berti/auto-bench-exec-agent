from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st

from benchmarks import BENCHMARKS, normalize_benchmark_selection
from process_cleanup import JOB_ID_ENV, cleanup_spawned_processes, pid_is_running


APP_ROOT = Path(__file__).resolve().parent
STATE_DIR = APP_ROOT / "state"
LOG_DIR = APP_ROOT / "logs"
STATUS_PATH = STATE_DIR / "status.json"
LOCK_PATH = STATE_DIR / "benchmark.lock"
LOG_PATH = LOG_DIR / "benchmark.log"
JOB_CONFIG_PATH = STATE_DIR / "job_config.json"
STARTING_GRACE_SECONDS = 120
PROVIDERS = [
    "openrouter",
    "openai",
    "google",
    "anthropic",
    "grok",
    "mistral",
    "deepinfra",
    "qwen",
    "nvidia",
    "perplexity",
    "groq",
]


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


def seconds_since(timestamp: Any) -> float | None:
    if not isinstance(timestamp, str):
        return None
    try:
        normalized = timestamp.replace("Z", "+00:00")
        started = datetime.fromisoformat(normalized)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - started).total_seconds()
    except ValueError:
        return None


def has_live_worker(status: dict[str, Any]) -> bool:
    return pid_is_running(status.get("worker_pid")) or pid_is_running(status.get("pid"))


def is_recent_starting_status(status: dict[str, Any]) -> bool:
    if status.get("state") != "starting":
        return False
    age = seconds_since(status.get("started_at"))
    return age is not None and age < STARTING_GRACE_SECONDS


def acquire_lock() -> bool:
    ensure_dirs()
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        status = read_status()
        if status.get("state") in {"starting", "running", "stopping"} and (has_live_worker(status) or is_recent_starting_status(status)):
            return False
        LOCK_PATH.unlink(missing_ok=True)
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as handler:
        handler.write(str(os.getpid()))
    return True


def release_lock() -> None:
    LOCK_PATH.unlink(missing_ok=True)


def normalize_status() -> dict[str, Any]:
    status = read_status()
    if status.get("state") == "stopping":
        cleanup = cleanup_spawned_processes(status)
        status = {
            **status,
            "state": "stopped",
            "finished_at": status.get("finished_at") or utc_now(),
            "returncode": None,
            "cleanup_at": utc_now(),
            **cleanup,
        }
        write_status(status)
        release_lock()
    elif status.get("state") in {"starting", "running"}:
        worker_pid = status.get("worker_pid")
        worker_missing = worker_pid is not None and not pid_is_running(worker_pid)
        execution_missing = not has_live_worker(status)
        if not (worker_missing or (execution_missing and not is_recent_starting_status(status))):
            return status
        cleanup = cleanup_spawned_processes(status)
        status = {
            **status,
            "state": "failed",
            "finished_at": utc_now(),
            "returncode": None,
            "error": "The benchmark process is no longer running.",
            "cleanup_at": utc_now(),
            **cleanup,
        }
        write_status(status)
        release_lock()
    return status


def append_log(message: str) -> None:
    ensure_dirs()
    with LOG_PATH.open("a", encoding="utf-8") as log_handler:
        log_handler.write(f"[{utc_now()}] {message}\n")


def stop_current_execution(status: dict[str, Any]) -> bool:
    if status.get("state") not in {"starting", "running", "stopping"}:
        return False

    stopping_status = {
        **status,
        "state": "stopping",
        "stop_requested_at": utc_now(),
    }
    write_status(stopping_status)
    append_log("stop requested from Streamlit UI")
    cleanup = cleanup_spawned_processes(
        stopping_status,
        exclude_pids={os.getpid()},
        trust_recorded_pids=True,
    )

    stopped_status = {
        **stopping_status,
        "state": "stopped",
        "finished_at": utc_now(),
        "returncode": None,
        "cleanup_at": utc_now(),
        **cleanup,
    }
    write_status(stopped_status)
    append_log("benchmark execution stopped")
    release_lock()
    return True


def cleanup_current_execution(status: dict[str, Any]) -> dict[str, Any]:
    was_busy = status.get("state") in {"starting", "running", "stopping"}
    cleanup_status = {
        **status,
        "state": "stopping" if was_busy else status.get("state", "idle"),
        "stop_requested_at": status.get("stop_requested_at") or (utc_now() if was_busy else None),
    }
    if was_busy:
        write_status(cleanup_status)
    append_log("spawned-thread cleanup requested from Streamlit UI")
    cleanup = cleanup_spawned_processes(
        cleanup_status,
        exclude_pids={os.getpid()},
        trust_recorded_pids=was_busy,
    )
    final_status = {
        **cleanup_status,
        "state": "stopped" if was_busy else cleanup_status.get("state", "idle"),
        "finished_at": utc_now() if was_busy else cleanup_status.get("finished_at"),
        "returncode": None if was_busy else cleanup_status.get("returncode"),
        "cleanup_at": utc_now(),
        **cleanup,
    }
    write_status(final_status)
    if was_busy:
        release_lock()
    append_log(
        "spawned-thread cleanup finished "
        f"(terminated={len(cleanup['terminated_pids'])}, remaining={len(cleanup['remaining_pids'])})"
    )
    return final_status


def build_worker_env(config: dict[str, Any], job_id: str) -> dict[str, str]:
    env = os.environ.copy()
    python_path_entries = [str(APP_ROOT)]
    if env.get("PYTHONPATH"):
        python_path_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path_entries)
    env[JOB_ID_ENV] = job_id
    env["AUTO_BENCH_THREAD_GUARD"] = "1"
    env["AUTO_BENCH_MAX_WORKERS"] = str(normalize_worker_threads(config.get("max_worker_threads")))
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


def submit_job(config: dict[str, Any]) -> bool:
    if not acquire_lock():
        return False
    ensure_dirs()
    LOG_PATH.write_text("", encoding="utf-8")
    with JOB_CONFIG_PATH.open("w", encoding="utf-8") as handler:
        json.dump(config, handler, indent=2)
        handler.write("\n")

    started_at = utc_now()
    job_id = uuid.uuid4().hex
    write_status(
        {
            "state": "starting",
            "job_id": job_id,
            "config": config,
            "started_at": started_at,
            "log_path": str(LOG_PATH),
        }
    )
    command = [sys.executable, str(APP_ROOT / "worker.py"), "--config-file", str(JOB_CONFIG_PATH)]
    try:
        with LOG_PATH.open("a", encoding="utf-8") as log_handler:
            log_handler.write(f"[{utc_now()}] starting worker: {' '.join(command)}\n")
            worker = subprocess.Popen(
                command,
                cwd=str(APP_ROOT),
                stdout=log_handler,
                stderr=subprocess.STDOUT,
                text=True,
                env=build_worker_env(config, job_id),
                **popen_detached_kwargs(),
            )
    except Exception as exc:
        write_status(
            {
                "state": "failed",
                "job_id": job_id,
                "worker_pid": None,
                "config": config,
                "started_at": started_at,
                "finished_at": utc_now(),
                "returncode": None,
                "error": str(exc),
                "log_path": str(LOG_PATH),
            }
        )
        release_lock()
        return False

    status = read_status()
    if status.get("state") == "starting":
        write_status({**status, "worker_pid": worker.pid})
    return True


def provider_index(provider: str | None) -> int:
    return PROVIDERS.index(provider) if provider in PROVIDERS else 0


def default_max_worker_threads() -> int:
    try:
        return max(1, int(os.environ.get("AUTO_BENCH_MAX_WORKERS", "60")))
    except ValueError:
        return 60


def normalize_worker_threads(value: Any) -> int:
    default_workers = default_max_worker_threads()
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default_workers


def default_selected_benchmarks(defaults: dict[str, Any]) -> list[str]:
    try:
        return list(normalize_benchmark_selection(defaults.get("benchmarks"), default_to_all=True))
    except ValueError:
        return list(BENCHMARKS)


def render_styles() -> None:
    st.markdown(
        """
        <style>
        div[data-testid="stForm"] { max-width: 980px; }
        div[data-testid="stForm"] label p { font-size: 1rem; }
        div[data-testid="stForm"] input,
        div[data-testid="stForm"] textarea,
        div[data-testid="stForm"] div[data-baseweb="select"] > div { min-height: 3rem; }
        .primary-fields input,
        .primary-fields div[data-baseweb="select"] > div {
            font-size: 1.15rem;
            min-height: 3.5rem;
        }
        .primary-fields label p {
            font-size: 1.1rem;
            font-weight: 650;
        }
        div[data-testid="stFormSubmitButton"] button {
            min-height: 3rem;
            width: 100%;
            font-size: 1.05rem;
        }
        .bench-spinner-wrap {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            margin: 0.25rem 0 1rem 0;
        }
        .bench-spinner {
            width: 28px;
            height: 28px;
            border: 3px solid #d8dee9;
            border-top-color: #2f6f73;
            border-radius: 50%;
            animation: bench-spin 0.9s linear infinite;
        }
        @keyframes bench-spin { to { transform: rotate(360deg); } }
        @media (max-width: 760px) {
            div[data-testid="stForm"] { max-width: 100%; }
            .primary-fields input,
            .primary-fields div[data-baseweb="select"] > div {
                font-size: 1rem;
                min-height: 3.25rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_form(disabled: bool, defaults: dict[str, Any]) -> dict[str, Any] | None:
    render_styles()
    with st.form("benchmark_config"):
        st.markdown('<div class="primary-fields">', unsafe_allow_html=True)
        model_name = st.text_input("LLM name", value=defaults.get("model_name", ""), disabled=disabled)
        provider = st.selectbox("Provider", PROVIDERS, index=provider_index(defaults.get("provider", "openrouter")), disabled=disabled)
        selected_benchmarks = st.multiselect(
            "Benchmarks",
            list(BENCHMARKS),
            default=default_selected_benchmarks(defaults),
            disabled=disabled,
        )
        st.markdown("</div>", unsafe_allow_html=True)

        values = {
            "base_model": "",
            "alias": "",
            "api_url": "",
            "api_key_env": "",
            "api_key_file": "",
            "reasoning_effort": "",
            "reasoning_enabled": False,
            "thinking_tokens": 0,
            "temperature_enabled": False,
            "temperature": 0.0,
            "max_tokens_enabled": False,
            "max_tokens": 0,
            "system_prompt": "",
            "add_prompt": "",
            "payload_json": "",
            "tools_json": "",
            "config_json": "",
            "config_file": "",
            "max_worker_threads": default_max_worker_threads(),
            "disable_git_clean": False,
        }

        with st.expander("Advanced configuration", expanded=False):
            values["base_model"] = st.text_input("Base model", value=defaults.get("base_model", ""), disabled=disabled)
            values["alias"] = st.text_input("Alias", value=defaults.get("alias", ""), disabled=disabled)
            left, right = st.columns(2)
            with left:
                values["api_url"] = st.text_input("API URL", value=defaults.get("api_url", ""), disabled=disabled)
                values["api_key_env"] = st.text_input("API key environment variable", value=defaults.get("api_key_env", ""), disabled=disabled)
                values["api_key_file"] = st.text_input("API key file", value=defaults.get("api_key_file", ""), disabled=disabled)
            with right:
                efforts = ["", "none", "low", "medium", "high", "xhigh"]
                values["reasoning_effort"] = st.selectbox(
                    "Reasoning effort",
                    efforts,
                    index=efforts.index(defaults.get("reasoning_effort", "")) if defaults.get("reasoning_effort", "") in efforts else 0,
                    disabled=disabled,
                )
                values["reasoning_enabled"] = st.checkbox("Reasoning enabled", value=bool(defaults.get("reasoning_enabled", False)), disabled=disabled)
                values["thinking_tokens"] = st.number_input("Thinking tokens", min_value=0, step=1000, value=int(defaults.get("thinking_tokens") or 0), disabled=disabled)
            left, right = st.columns(2)
            with left:
                values["temperature_enabled"] = st.checkbox("Temperature", value=defaults.get("temperature") is not None, disabled=disabled)
                values["temperature"] = st.number_input("Temperature value", min_value=0.0, max_value=2.0, step=0.1, value=float(defaults.get("temperature") or 0.0), disabled=disabled or not values["temperature_enabled"])
            with right:
                values["max_tokens_enabled"] = st.checkbox("Max tokens", value=defaults.get("max_tokens") is not None, disabled=disabled)
                values["max_tokens"] = st.number_input("Max tokens value", min_value=0, step=1000, value=int(defaults.get("max_tokens") or 0), disabled=disabled or not values["max_tokens_enabled"])
            values["system_prompt"] = st.text_area("System prompt", value=defaults.get("system_prompt", ""), disabled=disabled)
            values["add_prompt"] = st.text_area("Prompt suffix", value=defaults.get("add_prompt", ""), disabled=disabled)
            values["payload_json"] = st.text_area("Payload JSON", value=defaults.get("payload_json", ""), disabled=disabled)
            values["tools_json"] = st.text_area("Tools JSON", value=defaults.get("tools_json", ""), disabled=disabled)
            values["config_json"] = st.text_area("Config JSON", value=defaults.get("config_json", ""), disabled=disabled)
            values["config_file"] = st.text_input("Config file", value=defaults.get("config_file", ""), disabled=disabled)
            values["disable_git_clean"] = st.checkbox(
                "Disable git clean",
                value=bool(defaults.get("disable_git_clean", False)),
                disabled=disabled,
                help="Skip the executor repository's git clean preflight step. Git reset and git pull still run.",
            )
            values["max_worker_threads"] = st.number_input(
                "Max Python worker threads",
                min_value=1,
                step=1,
                value=normalize_worker_threads(defaults.get("max_worker_threads")),
                disabled=disabled,
                help="Upper-bounds Python threads and ThreadPoolExecutor workers in each active benchmark subprocess. Native library pools are limited separately.",
            )

        submitted = st.form_submit_button("Submit benchmark", disabled=disabled)
        if not submitted:
            return None

        return {
            "model_name": model_name.strip(),
            "provider": provider,
            "benchmarks": list(selected_benchmarks),
            "base_model": values["base_model"].strip(),
            "alias": values["alias"].strip(),
            "api_url": values["api_url"].strip(),
            "api_key_env": values["api_key_env"].strip(),
            "api_key_file": values["api_key_file"].strip(),
            "reasoning_effort": values["reasoning_effort"],
            "reasoning_enabled": values["reasoning_enabled"],
            "thinking_tokens": int(values["thinking_tokens"]) if values["thinking_tokens"] else None,
            "temperature": float(values["temperature"]) if values["temperature_enabled"] else None,
            "max_tokens": int(values["max_tokens"]) if values["max_tokens_enabled"] and values["max_tokens"] else None,
            "system_prompt": values["system_prompt"],
            "add_prompt": values["add_prompt"],
            "payload_json": values["payload_json"].strip(),
            "tools_json": values["tools_json"].strip(),
            "config_json": values["config_json"].strip(),
            "config_file": values["config_file"].strip(),
            "max_worker_threads": int(values["max_worker_threads"]),
            "disable_git_clean": bool(values["disable_git_clean"]),
        }


def read_log_tail(max_chars: int = 12000) -> str:
    try:
        return LOG_PATH.read_text(encoding="utf-8", errors="replace")[-max_chars:]
    except FileNotFoundError:
        return ""


def render_busy_spinner() -> None:
    st.markdown(
        '<div class="bench-spinner-wrap"><div class="bench-spinner"></div><span>Benchmark execution is running.</span></div>',
        unsafe_allow_html=True,
    )


def render_cleanup_result(status: dict[str, Any]) -> None:
    if not status.get("cleanup_at"):
        return
    remaining = status.get("remaining_pids") or []
    terminated = status.get("terminated_pids") or []
    message = f"Last spawned-thread cleanup: {status['cleanup_at']} ({len(terminated)} processes terminated)."
    if remaining:
        st.warning(f"{message} Processes still running: {', '.join(map(str, remaining))}")
    else:
        st.caption(message)


def main() -> None:
    ensure_dirs()
    st.set_page_config(page_title="Benchmark Executor", layout="wide")
    st.title("Benchmark Executor")

    status = normalize_status()
    busy = status.get("state") in {"starting", "running", "stopping"}
    defaults = status.get("config") if isinstance(status.get("config"), dict) else {}

    if busy:
        st.markdown("<meta http-equiv='refresh' content='10'>", unsafe_allow_html=True)
        render_styles()
        render_busy_spinner()
        stop_column, cleanup_column = st.columns(2)
        with stop_column:
            if st.button("Stop benchmark", type="primary", disabled=status.get("state") == "stopping", use_container_width=True):
                if stop_current_execution(status):
                    st.rerun()
                st.error("No active benchmark execution could be stopped.")
        with cleanup_column:
            if st.button("Clean up spawned threads", use_container_width=True):
                cleanup_current_execution(status)
                st.rerun()
        render_form(disabled=True, defaults=defaults)
        st.caption(f"Started: {status.get('started_at', '')}")
        log_tail = read_log_tail()
        if log_tail:
            st.code(log_tail, language="text")
        return

    if status.get("state") in {"completed", "failed", "stopped"}:
        message = f"Last run: {status.get('state')} at {status.get('finished_at', '')}"
        if status.get("returncode") is not None:
            message += f" (return code {status.get('returncode')})"
        st.info(message)

    cleanup_column, _ = st.columns(2)
    with cleanup_column:
        if st.button("Clean up spawned threads", use_container_width=True):
            status = cleanup_current_execution(status)
            st.rerun()
    render_cleanup_result(status)

    submitted_config = render_form(disabled=False, defaults=defaults)
    if submitted_config is None:
        return
    if not submitted_config["model_name"]:
        st.error("LLM name is required.")
        return
    try:
        submitted_config["benchmarks"] = list(normalize_benchmark_selection(submitted_config.get("benchmarks"), default_to_all=False))
    except ValueError as exc:
        st.error(str(exc))
        return
    if not submitted_config["benchmarks"]:
        st.error("Select at least one benchmark.")
        return
    for field in ("payload_json", "tools_json", "config_json"):
        if submitted_config.get(field):
            try:
                json.loads(submitted_config[field])
            except json.JSONDecodeError as exc:
                st.error(f"{field} is not valid JSON: {exc}")
                return
    if submit_job(submitted_config):
        st.rerun()
    status = read_status()
    if status.get("state") == "failed" and status.get("error"):
        st.error(f"Could not start benchmark worker: {status['error']}")
    else:
        st.error("A benchmark run is already active.")


if __name__ == "__main__":
    main()
