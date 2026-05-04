from __future__ import annotations

import os


def _thread_cap() -> int | None:
    raw_value = os.environ.get("AUTO_BENCH_MAX_WORKERS", "60").strip()
    if raw_value.lower() in {"", "0", "none", "off", "false"}:
        return None
    try:
        return max(60, int(raw_value))
    except ValueError:
        return 60


def _force_configured_workers() -> bool:
    raw_value = os.environ.get("AUTO_BENCH_FORCE_CONFIGURED_WORKERS", "1").strip().lower()
    return raw_value not in {"0", "false", "no", "off"}


def _install_threadpool_guard() -> None:
    if os.environ.get("AUTO_BENCH_THREAD_GUARD") != "1":
        return

    cap = _thread_cap()
    if cap is None:
        return

    from concurrent.futures import thread as thread_module

    if getattr(thread_module.ThreadPoolExecutor, "_auto_bench_guarded", False):
        return

    original_init = thread_module.ThreadPoolExecutor.__init__

    def guarded_init(self, max_workers=None, *args, **kwargs):
        if max_workers is None:
            configured_workers = cap
        elif max_workers <= 1:
            configured_workers = max_workers
        elif _force_configured_workers():
            configured_workers = cap
        else:
            configured_workers = min(max_workers, cap)
        return original_init(self, configured_workers, *args, **kwargs)

    thread_module.ThreadPoolExecutor.__init__ = guarded_init
    thread_module.ThreadPoolExecutor._auto_bench_guarded = True


def _install_thread_start_guard() -> None:
    if os.environ.get("AUTO_BENCH_THREAD_GUARD") != "1":
        return

    cap = _thread_cap()
    if cap is None:
        return

    import threading

    if getattr(threading.Thread, "_auto_bench_start_guarded", False):
        return

    original_start = threading.Thread.start
    original_run = threading.Thread.run
    semaphore = threading.BoundedSemaphore(cap)

    def guarded_run(self, *args, **kwargs):
        try:
            return original_run(self, *args, **kwargs)
        finally:
            if getattr(self, "_auto_bench_slot_acquired", False):
                self._auto_bench_slot_acquired = False
                semaphore.release()

    def guarded_start(self, *args, **kwargs):
        semaphore.acquire()
        self._auto_bench_slot_acquired = True
        try:
            return original_start(self, *args, **kwargs)
        except BaseException:
            self._auto_bench_slot_acquired = False
            semaphore.release()
            raise

    threading.Thread.run = guarded_run
    threading.Thread.start = guarded_start
    threading.Thread._auto_bench_start_guarded = True


_install_threadpool_guard()
_install_thread_start_guard()
