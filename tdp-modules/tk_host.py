# tk_host.py
#
# Dedicated Tk host running on its own thread, with a hidden root.
# All Tk operations must be executed on the Tk thread via tk_call().
#
# Guarantees:
# - Exactly one tk.Tk() instance (the hidden host root)
# - User windows are tk.Toplevel(root)
# - tk_call() is safe to use immediately after ensure_tk_host(); it waits for root readiness
#
# Optional:
# - tk_call_sync() to get a return value or raise exceptions on the caller thread

from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from typing import Any, Callable, Optional


_TK_THREAD: Optional[threading.Thread] = None
_TK_ROOT: Optional[tk.Tk] = None

# We signal when _TK_ROOT is created and the pump loop is active.
_TK_READY = threading.Event()

# Task queue to run on Tk thread.
_TK_QUEUE: "queue.Queue[_Task]" = queue.Queue()

# How often the pump runs (ms). 16ms ~= 60fps.
_PUMP_MS = 16


@dataclass
class _Task:
    fn: Callable[[tk.Tk], Any]
    done: Optional[threading.Event] = None
    out: Any = None
    err: Optional[BaseException] = None


def _tk_thread_main() -> None:
    global _TK_ROOT

    root = tk.Tk()
    root.withdraw()  # hidden host root
    _TK_ROOT = root

    # Root is now usable.
    _TK_READY.set()

    def pump() -> None:
        # Drain queue fully each tick.
        while True:
            try:
                task = _TK_QUEUE.get_nowait()
            except queue.Empty:
                break

            try:
                task.out = task.fn(root)
            except BaseException as e:
                task.err = e
            finally:
                if task.done is not None:
                    task.done.set()

        # Keep pumping.
        root.after(_PUMP_MS, pump)

    pump()
    root.mainloop()


def ensure_tk_host() -> None:
    global _TK_THREAD

    if _TK_THREAD and _TK_THREAD.is_alive():
        return

    _TK_READY.clear()
    _TK_THREAD = threading.Thread(target=_tk_thread_main, daemon=True, name="TkHostThread")
    _TK_THREAD.start()

    # Wait until Tk root is created so tk_call() can safely enqueue work.
    _TK_READY.wait(timeout=5.0)


def tk_call(fn: Callable[[tk.Tk], Any]) -> None:
    """
    Fire-and-forget: schedule fn(root) on Tk thread.
    Exceptions are swallowed (but printed to stderr) unless you use tk_call_sync().
    """
    ensure_tk_host()
    task = _Task(fn=fn, done=None)
    _TK_QUEUE.put(task)


def tk_call_sync(fn: Callable[[tk.Tk], Any], timeout: float = 10.0) -> Any:
    """
    Schedule fn(root) on Tk thread and wait for result.
    If fn raises, re-raise on caller thread.
    """
    ensure_tk_host()
    done = threading.Event()
    task = _Task(fn=fn, done=done)
    _TK_QUEUE.put(task)

    if not done.wait(timeout=timeout):
        raise TimeoutError("tk_call_sync timed out waiting for Tk task to complete")

    if task.err is not None:
        raise task.err

    return task.out


def get_tk_root() -> Optional[tk.Tk]:
    """
    Returns the hidden Tk root if created; otherwise None.
    """
    return _TK_ROOT
