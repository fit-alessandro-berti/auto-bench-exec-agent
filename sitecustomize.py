from __future__ import annotations

import os


def _thread_cap() -> int | None:
    raw_value = os.environ.get("AUTO_BENCH_MAX_WORKERS", "8").strip()
    if raw_value.lower() in {"", "0", "none", "off", "false"}:
        return None
    try:
        return max(1, int(raw_value))
    except ValueError:
        return 8


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
        bounded_workers = cap if max_workers is None else min(max_workers, cap)
        return original_init(self, bounded_workers, *args, **kwargs)

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
