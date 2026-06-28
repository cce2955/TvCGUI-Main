"""Extracted runtime module from :mod:`main`.

This module deliberately preserves the original function names and behavior so
`main.py` can remain a compatibility-oriented entry point while the subsystem
has a focused home.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import zipfile
from datetime import datetime

from tvcgui.core.paths import resource_path
from tvcgui.core.constants import MEM1_HI, MEM1_LO, MEM2_HI, MEM2_LO
from tvcgui.platform.dolphin import rbytes

try:
    import pyperclip
except ImportError:
    pyperclip = None

def u32be_from_block(block: bytes, off: int) -> int | None:
    if not block or off + 4 > len(block):
        return None
    return (
        (block[off] << 24)
        | (block[off + 1] << 16)
        | (block[off + 2] << 8)
        | block[off + 3]
    )


def _copy_to_clipboard(text: str) -> None:
    if not text:
        return
    if pyperclip is not None:
        try:
            pyperclip.copy(text)
            print(f"[copy] {text}")
            return
        except Exception as e:
            print(f"[copy] failed ({e!r}) -> {text}")
    print(f"[copy] (no pyperclip) -> {text}")


# Lightweight performance tracing. This intentionally rate-limits itself so
# a slow frame does not create console spam and make the stutter worse. Set
# TVC_PERF_LOG=0 to silence it.
PERF_LOG_ENABLED = os.environ.get("TVC_PERF_LOG", "1").strip().lower() not in {"0", "false", "off", "no"}
PERF_FRAME_WARN_MS = float(os.environ.get("TVC_PERF_FRAME_WARN_MS", "45"))
PERF_SECTION_WARN_MS = float(os.environ.get("TVC_PERF_SECTION_WARN_MS", "12"))
_PERF_LAST_LOG_TS: dict[str, float] = {}
_PERF_LAST_ELAPSED_MS: dict[str, float] = {}

# Automatic frame-data refreshes now use a cache-only worker and are debounced.
# Manual scans / opening the frame-data window can still run the full dynamic
# scanner when needed, but the main HUD loop must not keep launching dynamic
# scans during character changes or round reloads.
FD_AUTOSCAN_ENABLED = os.environ.get("TVC_FD_AUTOSCAN", "1").strip().lower() not in {"0", "false", "off", "no"}
FD_AUTOSCAN_DEBOUNCE_SEC = float(os.environ.get("TVC_FD_AUTOSCAN_DEBOUNCE_SEC", "0.35") or "0.35")
FD_AUTOSCAN_MIN_INTERVAL_SEC = float(os.environ.get("TVC_FD_AUTOSCAN_MIN_INTERVAL_SEC", "3.0") or "3.0")

# A newly seen fighter with no compact frame-data profile gets one background
# dynamic scan after the roster settles.  Known entries stay on the compact
# fast path.  This is deliberately roster/profile driven, not a scan on every
# move or every character refresh.
FD_BUILD_MISSING_PROFILES = os.environ.get("TVC_FD_BUILD_MISSING_PROFILES", "1").strip().lower() not in {"0", "false", "off", "no"}
FD_MISSING_PROFILE_BUILD_DELAY_SEC = float(os.environ.get("TVC_FD_MISSING_PROFILE_BUILD_DELAY_SEC", "0.75") or "0.75")
FD_MISSING_PROFILE_BUILD_MIN_INTERVAL_SEC = float(os.environ.get("TVC_FD_MISSING_PROFILE_BUILD_MIN_INTERVAL_SEC", "20.0") or "20.0")


def _perf_warn(label: str, start_perf: float, *, threshold_ms: float | None = None, min_interval: float = 1.0) -> None:
    if not PERF_LOG_ENABLED:
        return
    try:
        elapsed_ms = (time.perf_counter() - float(start_perf)) * 1000.0
        _PERF_LAST_ELAPSED_MS[str(label)] = round(float(elapsed_ms), 1)
    except Exception:
        return
    limit = PERF_SECTION_WARN_MS if threshold_ms is None else float(threshold_ms)
    if elapsed_ms < limit:
        return
    now_perf = time.perf_counter()
    last = float(_PERF_LAST_LOG_TS.get(label, 0.0) or 0.0)
    if now_perf - last < float(min_interval):
        return
    _PERF_LAST_LOG_TS[label] = now_perf
    try:
        print(f"[perf] {label} {elapsed_ms:.1f}ms")
    except Exception:
        pass


def _runtime_output_dir(*parts: str) -> str:
    """Writable app-adjacent output path for source and onefile builds."""
    try:
        if getattr(sys, "frozen", False):
            base = os.path.dirname(os.path.abspath(sys.executable))
        else:
            base = resource_path()
    except Exception:
        base = os.getcwd()
    return os.path.join(base, *parts)


def _start_memory_dump(mem_dump_state: dict) -> bool:
    """Start a background MEM1/MEM2 dump without freezing the GUI.

    The dump is written as a zip containing mem1.raw, mem2.raw, and a small
    manifest.  Files land beside the app under memory_dumps/, which keeps them
    easy to grab/upload for diffing while avoiding Git-tracked runtime JSON.
    """
    if bool(mem_dump_state.get("active", False)):
        return False

    def _worker():
        chunk_size = 0x100000  # 1 MiB keeps the UI responsive and progress smooth.
        started = time.time()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = _runtime_output_dir("memory_dumps")
        zip_path = os.path.join(out_dir, f"tvc_memdump_{stamp}.zip")
        manifest = {
            "created_at": stamp,
            "ranges": [],
            "errors": [],
            "duration_sec": None,
        }

        try:
            os.makedirs(out_dir, exist_ok=True)
            ranges = [
                ("mem1.raw", MEM1_LO, MEM1_HI),
                ("mem2.raw", MEM2_LO, MEM2_HI),
            ]
            total = sum(max(0, hi - lo) for _name, lo, hi in ranges)
            done = 0
            mem_dump_state.update({
                "active": True,
                "progress": 0.0,
                "label": "Dumping 0%",
                "path": zip_path,
                "error": "",
            })

            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
                for arc_name, lo, hi in ranges:
                    size = max(0, hi - lo)
                    bad_chunks = []
                    range_started = time.time()
                    with zf.open(arc_name, "w") as out_f:
                        addr = lo
                        while addr < hi:
                            n = min(chunk_size, hi - addr)
                            data = b""
                            try:
                                data = rbytes(addr, n)
                            except Exception as e:
                                manifest["errors"].append(f"{arc_name} read exception at 0x{addr:08X}: {e!r}")
                            if len(data) != n:
                                bad_chunks.append({
                                    "addr": f"0x{addr:08X}",
                                    "requested": int(n),
                                    "read": int(len(data) if data else 0),
                                })
                                # Keep raw offsets stable for diffing even if a read misses.
                                data = (data or b"") + (b"\x00" * max(0, n - len(data or b"")))
                            out_f.write(data)
                            addr += n
                            done += n
                            pct = (done / total) if total else 1.0
                            mem_dump_state["progress"] = pct
                            mem_dump_state["label"] = f"Dumping {int(pct * 100):d}%"
                    manifest["ranges"].append({
                        "file": arc_name,
                        "lo": f"0x{lo:08X}",
                        "hi": f"0x{hi:08X}",
                        "size": int(size),
                        "bad_chunks": bad_chunks,
                        "duration_sec": round(time.time() - range_started, 3),
                    })
                manifest["duration_sec"] = round(time.time() - started, 3)
                zf.writestr("manifest.json", json.dumps(manifest, indent=2))

            mem_dump_state.update({
                "active": False,
                "progress": 1.0,
                "label": "Dump complete",
                "path": zip_path,
                "last_done_time": time.time(),
                "error": "",
            })
            print(f"[memdump] wrote {zip_path}", flush=True)
        except Exception as e:
            mem_dump_state.update({
                "active": False,
                "label": "Dump failed",
                "error": repr(e),
                "last_done_time": time.time(),
            })
            print(f"[memdump] failed: {e!r}", flush=True)

    mem_dump_state.update({
        "active": True,
        "progress": 0.0,
        "label": "Dumping 0%",
        "error": "",
    })
    th = threading.Thread(target=_worker, name="TvCMemoryDump", daemon=True)
    mem_dump_state["thread"] = th
    th.start()
    return True

__all__ = [
    'u32be_from_block',
    '_copy_to_clipboard',
    'PERF_LOG_ENABLED',
    'PERF_FRAME_WARN_MS',
    'PERF_SECTION_WARN_MS',
    '_PERF_LAST_LOG_TS',
    '_PERF_LAST_ELAPSED_MS',
    'FD_AUTOSCAN_ENABLED',
    'FD_AUTOSCAN_DEBOUNCE_SEC',
    'FD_AUTOSCAN_MIN_INTERVAL_SEC',
    'FD_BUILD_MISSING_PROFILES',
    'FD_MISSING_PROFILE_BUILD_DELAY_SEC',
    'FD_MISSING_PROFILE_BUILD_MIN_INTERVAL_SEC',
    '_perf_warn',
    '_runtime_output_dir',
    '_start_memory_dump'
]
