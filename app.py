from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st


APP_ROOT = Path(__file__).resolve().parent
STATE_DIR = APP_ROOT / "state"
LOG_DIR = APP_ROOT / "logs"
STATUS_PATH = STATE_DIR / "status.json"
LOCK_PATH = STATE_DIR / "benchmark.lock"
LOG_PATH = LOG_DIR / "benchmark.log"
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
    tmp_path = STATUS_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as handler:
        json.dump(status, handler, indent=2)
        handler.write("\n")
    tmp_path.replace(STATUS_PATH)


def pid_is_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_lock() -> bool:
    ensure_dirs()
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        status = read_status()
        if status.get("state") == "running" and pid_is_running(status.get("pid")):
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
    if status.get("state") == "running" and not pid_is_running(status.get("pid")):
        status = {
            **status,
            "state": "failed",
            "finished_at": utc_now(),
            "returncode": None,
            "error": "The benchmark process is no longer running.",
        }
        write_status(status)
        release_lock()
    return status


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


def run_benchmarks(config: dict[str, Any]) -> None:
    command = build_command(config)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as log_handler:
            log_handler.write(f"\n[{utc_now()}] $ {' '.join(command)}\n")
            process = subprocess.Popen(
                command,
                cwd=str(APP_ROOT),
                stdout=log_handler,
                stderr=subprocess.STDOUT,
                text=True,
                env=os.environ.copy(),
            )
            write_status(
                {
                    "state": "running",
                    "pid": process.pid,
                    "command": command,
                    "config": config,
                    "started_at": utc_now(),
                    "log_path": str(LOG_PATH),
                }
            )
            returncode = process.wait()
            write_status(
                {
                    "state": "completed" if returncode == 0 else "failed",
                    "pid": process.pid,
                    "command": command,
                    "config": config,
                    "started_at": read_status().get("started_at"),
                    "finished_at": utc_now(),
                    "returncode": returncode,
                    "log_path": str(LOG_PATH),
                }
            )
            log_handler.write(f"[{utc_now()}] finished with return code {returncode}\n")
    except Exception as exc:
        write_status(
            {
                "state": "failed",
                "pid": None,
                "command": command,
                "config": config,
                "started_at": read_status().get("started_at"),
                "finished_at": utc_now(),
                "returncode": None,
                "error": str(exc),
                "log_path": str(LOG_PATH),
            }
        )
        with LOG_PATH.open("a", encoding="utf-8") as log_handler:
            log_handler.write(f"[{utc_now()}] failed to start or execute: {exc}\n")
    finally:
        release_lock()


def submit_job(config: dict[str, Any]) -> bool:
    if not acquire_lock():
        return False
    LOG_PATH.write_text("", encoding="utf-8")
    write_status({"state": "starting", "config": config, "started_at": utc_now(), "log_path": str(LOG_PATH)})
    thread = threading.Thread(target=run_benchmarks, args=(config,), daemon=True)
    thread.start()
    return True


def provider_index(provider: str | None) -> int:
    return PROVIDERS.index(provider) if provider in PROVIDERS else 0


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

        submitted = st.form_submit_button("Submit benchmark", disabled=disabled)
        if not submitted:
            return None

        return {
            "model_name": model_name.strip(),
            "provider": provider,
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


def main() -> None:
    ensure_dirs()
    st.set_page_config(page_title="Benchmark Executor", layout="wide")
    st.title("Benchmark Executor")

    status = normalize_status()
    busy = status.get("state") in {"starting", "running"}
    defaults = status.get("config") if isinstance(status.get("config"), dict) else {}

    if busy:
        st.markdown("<meta http-equiv='refresh' content='10'>", unsafe_allow_html=True)
        render_styles()
        render_busy_spinner()
        render_form(disabled=True, defaults=defaults)
        st.caption(f"Started: {status.get('started_at', '')}")
        log_tail = read_log_tail()
        if log_tail:
            st.code(log_tail, language="text")
        return

    if status.get("state") in {"completed", "failed"}:
        message = f"Last run: {status.get('state')} at {status.get('finished_at', '')}"
        if status.get("returncode") is not None:
            message += f" (return code {status.get('returncode')})"
        st.info(message)

    submitted_config = render_form(disabled=False, defaults=defaults)
    if submitted_config is None:
        return
    if not submitted_config["model_name"]:
        st.error("LLM name is required.")
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
    st.error("A benchmark run is already active.")


if __name__ == "__main__":
    main()
