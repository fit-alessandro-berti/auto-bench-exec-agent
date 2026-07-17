from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable


JOB_ID_ENV = "AUTO_BENCH_JOB_ID"


def coerce_pid(value: Any) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def pid_is_running(value: Any) -> bool:
    pid = coerce_pid(value)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False

    # A zombie has no live threads and only needs to be reaped by its parent.
    try:
        stat = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8")
        if stat[stat.rfind(")") + 2 :].startswith("Z"):
            return False
    except (FileNotFoundError, PermissionError, OSError):
        pass
    return True


def status_pids(status: dict[str, Any]) -> set[int]:
    pids: set[int] = set()
    for field in ("pid", "worker_pid"):
        pid = coerce_pid(status.get(field))
        if pid is not None:
            pids.add(pid)
    return pids


def job_process_ids(job_id: Any) -> set[int]:
    if os.name != "posix" or not job_id:
        return set()

    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return set()

    marker = f"{JOB_ID_ENV}={job_id}".encode()
    matches: set[int] = set()
    try:
        entries = proc_root.iterdir()
    except OSError:
        return matches

    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            environment = (entry / "environ").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        if marker in environment.split(b"\0"):
            matches.add(int(entry.name))
    return matches


def process_group_members(process_group_id: Any) -> set[int]:
    pgid = coerce_pid(process_group_id)
    if os.name != "posix" or pgid is None:
        return set()

    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return set()

    matches: set[int] = set()
    try:
        entries = proc_root.iterdir()
    except OSError:
        return matches
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8")
            fields = stat[stat.rfind(")") + 2 :].split()
            if len(fields) > 2 and int(fields[2]) == pgid and fields[0] != "Z":
                matches.add(int(entry.name))
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError, ValueError):
            continue
    return matches


def _terminate_pid(pid: int, *, force: bool) -> None:
    if os.name == "nt":
        command = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            command.append("/F")
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return

    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def cleanup_spawned_processes(
    status: dict[str, Any],
    *,
    exclude_pids: Iterable[int] = (),
    trust_recorded_pids: bool = False,
    grace_seconds: float = 5.0,
) -> dict[str, Any]:
    """Terminate processes belonging to one run, which also ends all of their threads.

    A unique job id is inherited by every benchmark subprocess. On Linux this lets
    cleanup find descendants even if their direct parent has already exited. Known
    top-level pids are also used while a run is active or by its owning worker.
    """

    excluded = {pid for value in exclude_pids if (pid := coerce_pid(value)) is not None}
    recorded = status_pids(status)
    benchmark_process_group = coerce_pid(status.get("pid"))
    job_id = status.get("job_id")
    all_seen: set[int] = set()

    def targets() -> set[int]:
        found = job_process_ids(job_id)
        if trust_recorded_pids:
            found.update(pid for pid in recorded if pid_is_running(pid))
            found.update(process_group_members(benchmark_process_group))
        found.difference_update(excluded)
        return {pid for pid in found if pid_is_running(pid)}

    pending = targets()
    all_seen.update(pending)
    for pid in pending:
        _terminate_pid(pid, force=False)

    deadline = time.monotonic() + max(0.0, grace_seconds)
    while pending and time.monotonic() < deadline:
        time.sleep(0.1)
        pending = targets()
        new_pids = pending - all_seen
        all_seen.update(pending)
        for pid in new_pids:
            _terminate_pid(pid, force=False)

    pending = targets()
    all_seen.update(pending)
    for pid in pending:
        _terminate_pid(pid, force=True)

    force_deadline = time.monotonic() + 1.0
    while pending and time.monotonic() < force_deadline:
        time.sleep(0.1)
        pending = targets()
        all_seen.update(pending)
        for pid in pending:
            _terminate_pid(pid, force=True)

    remaining = targets()
    all_seen.update(remaining)
    return {
        "found_pids": sorted(all_seen),
        "terminated_pids": sorted(all_seen - remaining),
        "remaining_pids": sorted(remaining),
    }
