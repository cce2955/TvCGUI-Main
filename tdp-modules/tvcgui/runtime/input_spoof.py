from __future__ import annotations

import threading
import time
from typing import Any

from tvcgui.platform.dolphin import rd32, wd32

P1_FIGHTER_PTR_ADDR = 0x803C9FCC
P1_INPUT_STRUCTS = (0x9246CD88, 0x92B6CDC8)

P1_SOURCE_STATUS_ADDR = 0x803F404C
P1_DECODED_SOURCE_ADDR = 0x803F4050
P1_RAW_SOURCE_ADDR = 0x803F4054

P1_MEM1_COPY_ADDRS = (0x80462BE8, 0x80462BF0, 0x803CA768, 0x803CAB28)

NEUTRAL_CURRENT = 0x00000800
BACK_CURRENT = 0x00400802
BACK_INPUT_BITS = 0x00400002
BACK_DECODED_BITS = 0x00000004
SOURCE_STATUS_ACTIVE = 0x00000007

# From the 20260709_221449 through 221519 true held-back dumps.
TRUE_HELD_MOVE_WORD = 0x84012001
TRUE_HELD_MOVE_ARG = 0x04000000
TRUE_HELD_MOVE_MODE = 0x00000005

NEUTRAL_MOVE_WORD = 0x0400A001
RELEASE_MOVE_WORD = 0x0404A001

_FRAME_SECONDS = 1.0 / 60.0
_MICRO_SLEEP = 0.0015
_LOCK = threading.RLock()
_WORKER: threading.Thread | None = None
_CANCEL = threading.Event()
_STATE: dict[str, Any] = {
    "active": False,
    "frames_requested": 0,
    "frames_written": 0,
    "last_ok": False,
    "last_error": "",
    "last_action": "Ready.",
    "started_at": 0.0,
    "finished_at": 0.0,
}


def _now() -> float:
    try:
        return time.monotonic()
    except Exception:
        return time.time()


def _set_state(**values: Any) -> None:
    with _LOCK:
        _STATE.update(values)


def get_input_spoof_state() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATE)


def _read_u32(addr: int, default: int = 0) -> int:
    try:
        val = rd32(int(addr))
        if val is None:
            return int(default)
        return int(val) & 0xFFFFFFFF
    except Exception:
        return int(default) & 0xFFFFFFFF


def _write_u32(addr: int, value: int) -> bool:
    try:
        return bool(wd32(int(addr), int(value) & 0xFFFFFFFF))
    except Exception:
        return False


def _fighter_base() -> int:
    base = _read_u32(P1_FIGHTER_PTR_ADDR, 0)
    if 0x90000000 <= base < 0x94000000:
        return base
    return 0


def _write_many(writes: list[tuple[int, int]]) -> tuple[int, int]:
    ok = 0
    for addr, value in writes:
        if _write_u32(addr, value):
            ok += 1
    return ok, len(writes)


def _struct_writes(prev_value: int, current_value: int, pressed_value: int, released_value: int, held_value: int) -> list[tuple[int, int]]:
    writes: list[tuple[int, int]] = []
    for base in P1_INPUT_STRUCTS:
        base_i = int(base)
        writes.extend([
            (base_i + 0x00, prev_value),
            (base_i + 0x04, current_value),
            (base_i + 0x08, pressed_value),
            (base_i + 0x0C, released_value),
            (base_i + 0x10, held_value),
        ])
    return writes


def _source_back_writes(include_status: bool) -> list[tuple[int, int]]:
    writes: list[tuple[int, int]] = []
    if include_status:
        writes.append((P1_SOURCE_STATUS_ADDR, SOURCE_STATUS_ACTIVE))
    writes.extend([
        (P1_DECODED_SOURCE_ADDR, BACK_DECODED_BITS),
        (P1_RAW_SOURCE_ADDR, BACK_INPUT_BITS),
    ])
    for addr in P1_MEM1_COPY_ADDRS:
        writes.append((addr, BACK_CURRENT))
    return writes


def _source_neutral_writes(include_status: bool) -> list[tuple[int, int]]:
    writes: list[tuple[int, int]] = []
    if include_status:
        writes.append((P1_SOURCE_STATUS_ADDR, SOURCE_STATUS_ACTIVE))
    writes.extend([
        (P1_DECODED_SOURCE_ADDR, 0x00000000),
        (P1_RAW_SOURCE_ADDR, 0x00000000),
    ])
    for addr in P1_MEM1_COPY_ADDRS:
        writes.append((addr, NEUTRAL_CURRENT))
    return writes


def _derived_held_writes() -> list[tuple[int, int]]:
    base = _fighter_base()
    if not base:
        return []
    return [
        (base + 0x58, TRUE_HELD_MOVE_MODE),
        (base + 0x60, TRUE_HELD_MOVE_WORD),
        (base + 0x64, TRUE_HELD_MOVE_ARG),
        (base + 0x13C8, BACK_CURRENT),
        (base + 0x13CC, BACK_CURRENT),
        (base + 0x13D0, 0x00000000),
        (base + 0x13D4, 0x00000000),
        (base + 0x13D8, 0x00000000),
    ]


def _derived_release_writes() -> list[tuple[int, int]]:
    base = _fighter_base()
    if not base:
        return []
    return [
        (base + 0x58, 0x00000001),
        (base + 0x60, RELEASE_MOVE_WORD),
        (base + 0x64, 0x00000000),
        (base + 0x13C8, BACK_CURRENT),
        (base + 0x13CC, NEUTRAL_CURRENT),
        (base + 0x13D0, 0x00000000),
        (base + 0x13D4, BACK_INPUT_BITS),
        (base + 0x13D8, 0x00000000),
    ]


def _derived_neutral_writes() -> list[tuple[int, int]]:
    base = _fighter_base()
    if not base:
        return []
    return [
        (base + 0x58, 0x00000001),
        (base + 0x60, NEUTRAL_MOVE_WORD),
        (base + 0x64, 0x00000000),
        (base + 0x13C8, NEUTRAL_CURRENT),
        (base + 0x13CC, NEUTRAL_CURRENT),
        (base + 0x13D0, 0x00000000),
        (base + 0x13D4, 0x00000000),
        (base + 0x13D8, 0x00000000),
    ]


def _true_hold_writes(include_source: bool, include_status: bool) -> list[tuple[int, int]]:
    writes: list[tuple[int, int]] = []
    if include_source:
        writes.extend(_source_back_writes(include_status))
    writes.extend(_struct_writes(BACK_CURRENT, BACK_CURRENT, 0x00000000, 0x00000000, 0x00000000))
    writes.extend(_derived_held_writes())
    return writes


def _release_writes(include_source: bool, include_status: bool) -> list[tuple[int, int]]:
    writes: list[tuple[int, int]] = []
    if include_source:
        writes.extend(_source_neutral_writes(include_status))
    writes.extend(_struct_writes(BACK_CURRENT, NEUTRAL_CURRENT, 0x00000000, BACK_INPUT_BITS, 0x00000000))
    writes.extend(_derived_release_writes())
    return writes


def _neutral_writes(include_source: bool, include_status: bool) -> list[tuple[int, int]]:
    writes: list[tuple[int, int]] = []
    if include_source:
        writes.extend(_source_neutral_writes(include_status))
    writes.extend(_struct_writes(NEUTRAL_CURRENT, NEUTRAL_CURRENT, 0x00000000, 0x00000000, 0x00000000))
    writes.extend(_derived_neutral_writes())
    return writes


def _read_probe() -> dict[str, Any]:
    base = _fighter_base()
    out: dict[str, Any] = {"fighter_base": base}
    for idx, struct_base in enumerate(P1_INPUT_STRUCTS, start=1):
        prefix = f"mem2_{idx}"
        out[prefix] = [
            _read_u32(struct_base + 0x00),
            _read_u32(struct_base + 0x04),
            _read_u32(struct_base + 0x08),
            _read_u32(struct_base + 0x0C),
            _read_u32(struct_base + 0x10),
        ]
    out["source"] = [
        _read_u32(P1_SOURCE_STATUS_ADDR),
        _read_u32(P1_DECODED_SOURCE_ADDR),
        _read_u32(P1_RAW_SOURCE_ADDR),
    ]
    out["mem1_copies"] = [_read_u32(addr) for addr in P1_MEM1_COPY_ADDRS]
    if base:
        out["derived"] = [
            _read_u32(base + 0x58),
            _read_u32(base + 0x60),
            _read_u32(base + 0x64),
            _read_u32(base + 0x13C8),
            _read_u32(base + 0x13CC),
            _read_u32(base + 0x13D0),
            _read_u32(base + 0x13D4),
            _read_u32(base + 0x13D8),
        ]
    return out


def _print_probe(prefix: str) -> None:
    p = _read_probe()
    def hx(v: int) -> str:
        return f"{int(v) & 0xFFFFFFFF:08X}"
    m1 = p.get("mem2_1") or []
    m2 = p.get("mem2_2") or []
    src = p.get("source") or []
    der = p.get("derived") or []
    print(
        f"[input truehold] {prefix} base={hx(p.get('fighter_base') or 0)} "
        f"a={','.join(hx(v) for v in m1)} b={','.join(hx(v) for v in m2)} "
        f"src={','.join(hx(v) for v in src)} der={','.join(hx(v) for v in der)}",
        flush=True,
    )


def _worker_run(frames: int, *, include_source: bool, include_status: bool, label: str) -> None:
    requested = max(1, int(frames))
    _set_state(
        active=True,
        frames_requested=requested,
        frames_written=0,
        last_ok=False,
        last_error="",
        last_action=f"Running {label} for {requested} frames.",
        started_at=_now(),
        finished_at=0.0,
    )

    total_ok = 0
    total_writes = 0
    frames_written = 0
    try:
        print("\n[input truehold] ===== TRUE HELD BACK START =====", flush=True)
        print(f"[input truehold] mode={label} frames={requested}", flush=True)
        print("[input truehold] target packet prev=cur=00400802 press=0 rel=0 held=0", flush=True)
        print("[input truehold] target derived +58=5 +60=84012001 +64=04000000 +13D8=0", flush=True)
        _print_probe("initial")

        for frame in range(requested):
            if _CANCEL.is_set():
                break
            frame_start = _now()
            frame_ok = 0
            frame_total = 0
            while True:
                ok, total = _write_many(_true_hold_writes(include_source, include_status))
                frame_ok += ok
                frame_total += total
                if _CANCEL.is_set():
                    break
                if _now() - frame_start >= _FRAME_SECONDS:
                    break
                time.sleep(_MICRO_SLEEP)
            total_ok += frame_ok
            total_writes += frame_total
            frames_written += 1
            if frames_written in (1, 15, 30, 45, requested):
                _print_probe(f"f={frames_written:03d}")
            _set_state(frames_written=frames_written, last_action=f"{label} frame {frames_written}/{requested}")

        if not _CANCEL.is_set():
            ok, total = _write_many(_release_writes(include_source, include_status))
            total_ok += ok
            total_writes += total
            time.sleep(_FRAME_SECONDS)
            ok, total = _write_many(_neutral_writes(include_source, include_status))
            total_ok += ok
            total_writes += total

        _print_probe("final")
        print(f"[input truehold] writes_ok={total_ok}/{total_writes} frames={frames_written}/{requested}", flush=True)
        print("[input truehold] ===== TRUE HELD BACK END =====\n", flush=True)

        success = frames_written == requested and total_ok > 0
        _set_state(
            active=False,
            frames_written=frames_written,
            last_ok=bool(success),
            last_error="" if success else f"Only {total_ok}/{total_writes} writes succeeded.",
            last_action=(f"Finished {label} {frames_written}/{requested} frames." if success else f"{label} test had write misses: {total_ok}/{total_writes}."),
            finished_at=_now(),
            last_probe=_read_probe(),
        )
    except Exception as e:
        try:
            _write_many(_neutral_writes(include_source, include_status))
        except Exception:
            pass
        _set_state(
            active=False,
            last_ok=False,
            last_error=repr(e),
            last_action=f"{label} failed.",
            finished_at=_now(),
        )


def _start_worker(frames: int, *, include_source: bool, include_status: bool, label: str) -> dict[str, Any]:
    global _WORKER
    with _LOCK:
        if _STATE.get("active"):
            return dict(_STATE)
        _CANCEL.clear()
        _WORKER = threading.Thread(
            target=_worker_run,
            kwargs={"frames": int(frames), "include_source": bool(include_source), "include_status": bool(include_status), "label": str(label)},
            daemon=True,
            name="InputTrueHeldBack",
        )
        _WORKER.start()
        state = dict(_STATE)
        state["last_action"] = f"Queued {label} for {int(frames)} frames."
        return state


def request_true_held_back(frames: int = 60) -> dict[str, Any]:
    return _start_worker(frames, include_source=True, include_status=False, label="True held back")


def request_true_held_back_status(frames: int = 60) -> dict[str, Any]:
    return _start_worker(frames, include_source=True, include_status=True, label="True held back with status")


def request_true_held_packet_only(frames: int = 60) -> dict[str, Any]:
    return _start_worker(frames, include_source=False, include_status=False, label="True held packet only")


def request_p1_back(frames: int = 60) -> dict[str, Any]:
    return request_true_held_back(frames)


def cancel_input_spoof() -> dict[str, Any]:
    _CANCEL.set()
    try:
        _write_many(_neutral_writes(True, False))
    except Exception:
        pass
    _set_state(active=False, last_action="Input spoof cancelled and neutral written.", finished_at=_now())
    return get_input_spoof_state()
