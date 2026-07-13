from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback
from pathlib import Path


def _tdp_root() -> Path:
    return Path(__file__).resolve().parents[2]


def launch_advantage_popup() -> int:
    """Launch the Advantage Matrix in an isolated Python process."""
    if getattr(sys, "frozen", False):
        from tvcgui.ui.advantage_window import open_advantage_window
        open_advantage_window(None, None)
        return 0

    script = Path(__file__).resolve()
    root = _tdp_root()
    env = os.environ.copy()
    old_pythonpath = str(env.get("PYTHONPATH") or "").strip()
    root_text = str(root)
    env["PYTHONPATH"] = root_text if not old_pythonpath else root_text + os.pathsep + old_pythonpath

    creationflags = 0
    if sys.platform == "win32":
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))

    proc = subprocess.Popen(
        [sys.executable, str(script), "--child"],
        cwd=str(root),
        env=env,
        creationflags=creationflags,
        close_fds=(sys.platform != "win32"),
    )
    return int(proc.pid or 0)


def _show_failure_window(message: str) -> None:
    try:
        import tkinter as tk

        root = tk.Tk()
        root.title("Advantage Matrix Error")
        root.geometry("760x240")
        root.minsize(620, 180)
        root.configure(bg="#151821")
        tk.Label(
            root,
            text="Advantage Matrix failed to open",
            bg="#151821",
            fg="#e2e6ee",
            font=("Segoe UI", 14, "bold"),
            anchor="w",
        ).pack(fill="x", padx=18, pady=(18, 8))
        tk.Label(
            root,
            text=message,
            bg="#151821",
            fg="#d08090",
            font=("Consolas", 10),
            justify="left",
            anchor="nw",
            wraplength=710,
        ).pack(fill="both", expand=True, padx=18, pady=(0, 18))
        root.attributes("-topmost", True)
        root.after(400, lambda: root.attributes("-topmost", False))
        root.mainloop()
    except Exception:
        pass


def _run_child() -> int:
    try:
        import tvcgui.ui.advantage_window as advantage_window

        opened = bool(advantage_window.open_advantage_window(None, None))
        if not opened:
            raise RuntimeError("open_advantage_window returned False")

        deadline = time.time() + 12.0
        while time.time() < deadline:
            if getattr(advantage_window, "_ADV_TK_WIN", None) is not None:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("the popup host did not create a window within 12 seconds")

        while getattr(advantage_window, "_ADV_TK_WIN", None) is not None:
            time.sleep(0.10)
        return 0
    except Exception as exc:
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        print(f"[advantage popup] failed: {exc!r}", flush=True)
        _show_failure_window(detail[-5000:])
        return 1


if __name__ == "__main__":
    if "--child" in sys.argv:
        raise SystemExit(_run_child())
    raise SystemExit(launch_advantage_popup())
