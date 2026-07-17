from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

import app
import cli_execute
import worker
from process_cleanup import JOB_ID_ENV, cleanup_spawned_processes, job_process_ids, process_group_members


APP_ROOT = Path(__file__).resolve().parent


class ExecutionConfigurationTests(unittest.TestCase):
    def test_disable_git_clean_is_forwarded_to_benchmarks(self) -> None:
        forwarded = cli_execute.build_forwarded_args(
            [
                "model",
                "--benchmark",
                "d-bench",
                "--provider",
                "openai",
                "--max-worker-threads",
                "3",
                "--disable-git-clean",
                "--dry-run",
            ]
        )
        self.assertEqual(forwarded, ["model", "--provider", "openai", "--disable-git-clean"])

    def test_worker_command_propagates_execution_options(self) -> None:
        command = worker.build_command(
            {
                "model_name": "model",
                "benchmarks": ["d-bench"],
                "max_worker_threads": 3,
                "disable_git_clean": True,
            }
        )
        self.assertIn("--max-worker-threads", command)
        self.assertIn("3", command)
        self.assertIn("--disable-git-clean", command)

    def test_requested_worker_count_is_not_clamped_to_sixty(self) -> None:
        worker_env = app.build_worker_env({"max_worker_threads": 3}, "job-id")
        child_env = worker.build_child_env({"max_worker_threads": 3})
        cli_env = cli_execute.build_child_env(3)
        for environment in (worker_env, child_env, cli_env):
            self.assertEqual(environment["AUTO_BENCH_MAX_WORKERS"], "3")
            self.assertEqual(environment["EVALUATION_MAX_WORKERS"], "3")

    def test_disable_git_clean_skips_only_clean(self) -> None:
        with mock.patch.object(cli_execute.subprocess, "run") as run:
            cli_execute.run_app_git_preflight(dry_run=False, disable_git_clean=True)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands, [["git", "reset", "--hard", "HEAD"], ["git", "pull"]])

    def test_thread_pool_guard_is_an_upper_bound(self) -> None:
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(APP_ROOT),
                "AUTO_BENCH_THREAD_GUARD": "1",
                "AUTO_BENCH_MAX_WORKERS": "3",
            }
        )
        code = (
            "from concurrent.futures import ThreadPoolExecutor; "
            "print(ThreadPoolExecutor(max_workers=2)._max_workers, "
            "ThreadPoolExecutor(max_workers=10)._max_workers)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "2 3")


@unittest.skipUnless(os.name == "posix" and Path("/proc").is_dir(), "requires Linux /proc")
class ProcessCleanupTests(unittest.TestCase):
    def test_cleanup_finds_and_terminates_inherited_job_processes(self) -> None:
        job_id = uuid.uuid4().hex
        environment = os.environ.copy()
        environment[JOB_ID_ENV] = job_id
        code = "import subprocess,time; subprocess.Popen(['sleep','30']); time.sleep(30)"
        process = subprocess.Popen([sys.executable, "-c", code], env=environment, start_new_session=True)
        try:
            deadline = time.monotonic() + 3
            while len(job_process_ids(job_id)) < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
            result = cleanup_spawned_processes(
                {"job_id": job_id, "pid": process.pid},
                trust_recorded_pids=True,
                grace_seconds=1,
            )
            process.wait(timeout=3)
            self.assertGreaterEqual(len(result["terminated_pids"]), 2)
            self.assertEqual(result["remaining_pids"], [])
            self.assertEqual(job_process_ids(job_id), set())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_cleanup_also_finds_descendants_that_clear_the_job_environment(self) -> None:
        job_id = uuid.uuid4().hex
        environment = os.environ.copy()
        environment[JOB_ID_ENV] = job_id
        code = "import os,subprocess,time; subprocess.Popen(['sleep','30'], env={'PATH': os.environ['PATH']}); time.sleep(30)"
        process = subprocess.Popen([sys.executable, "-c", code], env=environment, start_new_session=True)
        try:
            deadline = time.monotonic() + 3
            while len(process_group_members(process.pid)) < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
            result = cleanup_spawned_processes(
                {"job_id": job_id, "pid": process.pid},
                trust_recorded_pids=True,
                grace_seconds=1,
            )
            process.wait(timeout=3)
            self.assertGreaterEqual(len(result["terminated_pids"]), 2)
            self.assertEqual(result["remaining_pids"], [])
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


if __name__ == "__main__":
    unittest.main()
