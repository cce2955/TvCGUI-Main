from __future__ import annotations

import struct
import threading
import time
from typing import Any

from tvcgui.platform.dolphin import rd32, wd32

P1_FIGHTER_PTR_ADDR = 0x803C9FCC

# These three were the only non-fighter MEM2 words that tracked Ryu 5A as
# idle -> 0x100 -> idle in the 20260709_222457 through 222618 dumps.
ACTION_ECHO_ADDR = 0x908D39C8
ACTION_REQUEST_CANDIDATES = (0x908D3A2C, 0x92503E00)

# P1 fighter action bundle, relative to the dynamic fighter base.
OFF_STATE_MODE = 0x58
OFF_MOVE_WORD = 0x60
OFF_MOVE_ARG = 0x64
OFF_STATUS_GATE = 0x74
OFF_ATTACK_STATUS = 0x88
OFF_ANIM_FRAME_FLOAT = 0x1D8
OFF_ACTION_A = 0x1E8
OFF_ACTION_B = 0x1EC
OFF_UNKNOWN_204 = 0x204
OFF_ACTION_STATE = 0x214
OFF_ACTION_C = 0x218
OFF_FRAME_A = 0x21C
OFF_FRAME_B = 0x220
OFF_HITBOX_GATE = 0x224
OFF_PUSH_FLOAT = 0x22C
OFF_FLAGS_240 = 0x240
OFF_DAMAGE_SCALE = 0x24C
OFF_HITBOX_ID = 0x254
OFF_HITBOX_KIND = 0x258
OFF_HITBOX_ON = 0x260
OFF_HITBOX_META = 0x264

# Visual animation controller fields seen in the real 5A dumps.
# The previous action bundle changed the action and hitbox words, but these
# stayed idle, so the model did not visibly start the move.
OFF_VISUAL_FRAME_A = 0x1314
OFF_VISUAL_FRAME_B = 0x1378
OFF_VISUAL_FRAME_C = 0x13C4
OFF_INPUT_SHADOW = 0x1380
VISUAL_SLOT_OFFSETS = (
    0x1424, 0x1428, 0x1464, 0x1468, 0x14A4, 0x14A8, 0x14E4, 0x14E8,
    0x1524, 0x1528, 0x1564, 0x1568, 0x15A4, 0x15A8, 0x15B4, 0x15B8,
    0x15C4, 0x15C8, 0x1614, 0x1618, 0x1624, 0x1628, 0x1634, 0x1638,
    0x1644, 0x1648, 0x1684, 0x1688, 0x1694, 0x1698, 0x16A4, 0x16A8,
    0x16C4, 0x16C8,
)
VISUAL_5A_SLOT_VALUE = 0x00000616

# Active-frame collision and effect controller pieces that stayed stable across
# the mid 5A dumps. These are deliberately kept out of the early frames.
ACTIVE_5A_STATIC_WRITES = (
    (0x02AC, 0x40C00000),
    (0x04E0, 0xBD23D70A), (0x04E4, 0x3D4CCCCD),
    (0x0500, 0x00000052), (0x0504, 0x80EA89F4), (0x0508, 0x80EA89F4),
    (0x053C, 0x3E4CCCCC),
    (0x0540, 0x00000052), (0x0544, 0x80EA8774), (0x0548, 0x80EA8774),
    (0x0554, 0x3DCCCCCC), (0x0560, 0x3DCCCCCC), (0x057C, 0x3E4CCCCC),
    (0x0B00, 0xBF84CDCD), (0x0B04, 0x3FA549FF), (0x0B08, 0xBEB290E3),
    (0x0B0C, 0xBFA2B7DD), (0x0B10, 0x3F9165B5), (0x0B14, 0xBED67C55),
    (0x19A0, 0x00000005), (0x19AC, 0xFFFFFF00), (0x19B0, 0x00000200),
    (0x19C8, 0x00000005), (0x19D4, 0xFFFFFF00), (0x19D8, 0x00000100),
    (0x19DC, 0x00000005), (0x19E8, 0xFFFFFF00), (0x19EC, 0x00000100),
    (0x19F0, 0x00000005), (0x19FC, 0xFFFFFF00), (0x1A00, 0x00000200),
    (0x1A04, 0x00000005), (0x1A10, 0xFFFFFF00), (0x1A14, 0x00000100),
    (0x1A18, 0x00000005), (0x1A24, 0xFFFFFF00), (0x1A28, 0x00000100),
    (0x1ED8, 0xFFFFFFFF), (0x1EDC, 0x00000100), (0x20D8, 0x00000001),
)

IDLE_ACTION = 0x00000001
MOVE_5A = 0x00000100
MOVE_5B = 0x00000101
MOVE_HADO = 0x00000130

_FRAME_SECONDS = 1.0 / 60.0
_LOCK = threading.RLock()
_CANCEL = threading.Event()
_WORKER: threading.Thread | None = None
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


def get_action_force_state() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATE)


def _read_u32(addr: int, default: int = 0) -> int:
    try:
        val = rd32(int(addr))
        if val is None:
            return int(default) & 0xFFFFFFFF
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


def _f32_word(value: float) -> int:
    return struct.unpack(">I", struct.pack(">f", float(value)))[0]


def _write_many(writes: list[tuple[int, int]]) -> tuple[int, int]:
    ok = 0
    total = 0
    for addr, value in writes:
        total += 1
        if _write_u32(addr, value):
            ok += 1
    return ok, total


def _watch_values() -> dict[str, int]:
    base = _fighter_base()
    vals: dict[str, int] = {"base": base}
    vals["echo"] = _read_u32(ACTION_ECHO_ADDR)
    vals["req_a"] = _read_u32(ACTION_REQUEST_CANDIDATES[0])
    vals["req_b"] = _read_u32(ACTION_REQUEST_CANDIDATES[1])
    if base:
        for name, off in (
            ("mode", OFF_STATE_MODE),
            ("move_word", OFF_MOVE_WORD),
            ("move_arg", OFF_MOVE_ARG),
            ("gate", OFF_STATUS_GATE),
            ("atk_status", OFF_ATTACK_STATUS),
            ("anim_f", OFF_ANIM_FRAME_FLOAT),
            ("act_a", OFF_ACTION_A),
            ("act_b", OFF_ACTION_B),
            ("u204", OFF_UNKNOWN_204),
            ("act_state", OFF_ACTION_STATE),
            ("act_c", OFF_ACTION_C),
            ("frame_a", OFF_FRAME_A),
            ("frame_b", OFF_FRAME_B),
            ("hit_gate", OFF_HITBOX_GATE),
            ("push", OFF_PUSH_FLOAT),
            ("flags240", OFF_FLAGS_240),
            ("scale", OFF_DAMAGE_SCALE),
            ("hit_id", OFF_HITBOX_ID),
            ("hit_kind", OFF_HITBOX_KIND),
            ("hit_on", OFF_HITBOX_ON),
            ("hit_meta", OFF_HITBOX_META),
            ("vis_a", OFF_VISUAL_FRAME_A),
            ("vis_b", OFF_VISUAL_FRAME_B),
            ("vis_c", OFF_VISUAL_FRAME_C),
            ("input_shadow", OFF_INPUT_SHADOW),
        ):
            vals[name] = _read_u32(base + off)
        vals["slot1424"] = _read_u32(base + 0x1424)
        vals["slot1464"] = _read_u32(base + 0x1464)
        vals["fx19a0"] = _read_u32(base + 0x19A0)
        vals["fx1edc"] = _read_u32(base + 0x1EDC)
    return vals


def _fmt_watch(vals: dict[str, int]) -> str:
    base = int(vals.get("base", 0) or 0)
    if not base:
        return "base=00000000"
    return (
        f"base={base:08X} "
        f"act={vals.get('act_a', 0):08X}/{vals.get('act_b', 0):08X}/{vals.get('act_c', 0):08X} "
        f"state={vals.get('act_state', 0):08X} frames={vals.get('frame_a', 0):08X}/{vals.get('frame_b', 0):08X} "
        f"mode={vals.get('mode', 0):08X} move={vals.get('move_word', 0):08X}/{vals.get('move_arg', 0):08X} "
        f"hit={vals.get('hit_gate', 0):08X}/{vals.get('flags240', 0):08X}/{vals.get('hit_on', 0):08X} "
        f"vis={vals.get('vis_a', 0):08X}/{vals.get('vis_b', 0):08X}/{vals.get('vis_c', 0):08X} "
        f"slots={vals.get('slot1424', 0):08X}/{vals.get('slot1464', 0):08X} "
        f"fx={vals.get('fx19a0', 0):08X}/{vals.get('fx1edc', 0):08X} "
        f"globals={vals.get('echo', 0):08X}/{vals.get('req_a', 0):08X}/{vals.get('req_b', 0):08X}"
    )


def _request_writes(move_id: int, include_echo: bool = False) -> list[tuple[int, int]]:
    move = int(move_id) & 0xFFFFFFFF
    writes = [(addr, move) for addr in ACTION_REQUEST_CANDIDATES]
    if include_echo:
        writes.insert(0, (ACTION_ECHO_ADDR, move))
    return writes


def _starter_bundle_writes(move_id: int = MOVE_5A) -> list[tuple[int, int]]:
    base = _fighter_base()
    if not base:
        return []
    move = int(move_id) & 0xFFFFFFFF
    return [
        # First visible 5A frame from 20260709_222549.
        (base + OFF_STATE_MODE, 0x40400001),
        (base + OFF_MOVE_WORD, 0x00000000),
        (base + OFF_MOVE_ARG, 0x00000000),
        (base + OFF_STATUS_GATE, 0x00000000),
        (base + OFF_ATTACK_STATUS, 0x00000000),
        (base + OFF_ANIM_FRAME_FLOAT, 0x40000000),
        (base + OFF_ACTION_A, move),
        (base + OFF_ACTION_B, move),
        (base + OFF_UNKNOWN_204, 0x00000000),
        (base + OFF_ACTION_STATE, 0x00000001),
        (base + OFF_ACTION_C, move),
        (base + OFF_FRAME_A, 0x00000002),
        (base + OFF_FRAME_B, 0x00000002),
        (base + OFF_HITBOX_GATE, 0x00000000),
        (base + OFF_PUSH_FLOAT, 0xBF800000),
        (base + OFF_FLAGS_240, 0x00000009),
        (base + OFF_DAMAGE_SCALE, 0x00000064),
        (base + OFF_HITBOX_ID, 0xFFFFFFFF),
        (base + OFF_HITBOX_KIND, 0x00000000),
        (base + OFF_HITBOX_ON, 0x00000000),
        (base + OFF_HITBOX_META, 0xFFFFFFFF),
    ]


def _timeline_5a_writes(frame: int) -> list[tuple[int, int]]:
    base = _fighter_base()
    if not base:
        return []
    f = max(2, int(frame))
    active = f >= 8
    # Keep this as a reproducer of the natural 5A bundle, not a perfect
    # animation engine. The goal is to see whether the native hitbox layer
    # keys off the action bundle or only off the transition dispatcher.
    writes = [
        (base + OFF_STATE_MODE, 0x40400001),
        (base + OFF_MOVE_WORD, 0x00000000),
        (base + OFF_MOVE_ARG, 0x00000000),
        (base + OFF_STATUS_GATE, 0x00020000 if active else 0x00000000),
        (base + OFF_ATTACK_STATUS, 0x00001005 if active else 0x00000000),
        (base + OFF_ANIM_FRAME_FLOAT, _f32_word(float(f))),
        (base + OFF_ACTION_A, MOVE_5A),
        (base + OFF_ACTION_B, MOVE_5A),
        (base + OFF_UNKNOWN_204, 0x00000000),
        (base + OFF_ACTION_STATE, 0x00000001),
        (base + OFF_ACTION_C, MOVE_5A),
        (base + OFF_FRAME_A, f),
        (base + OFF_FRAME_B, f),
        (base + OFF_HITBOX_GATE, 0x00000018 if active else 0x00000000),
        (base + OFF_PUSH_FLOAT, 0xBDCCCCCD if active else 0xBF800000),
        (base + OFF_FLAGS_240, 0x08000009 if active else 0x00000009),
        (base + OFF_DAMAGE_SCALE, 0x00000320 if active else 0x00000064),
        (base + OFF_HITBOX_ID, 0x0000000C if active else 0xFFFFFFFF),
        (base + OFF_HITBOX_KIND, 0x00000009 if active else 0x00000000),
        (base + OFF_HITBOX_ON, 0x00000004 if active else 0x00000000),
        (base + OFF_HITBOX_META, 0x00000006 if active else 0xFFFFFFFF),
    ]
    return writes


def _visual_controller_5a_writes(frame: int) -> list[tuple[int, int]]:
    base = _fighter_base()
    if not base:
        return []
    f = max(1, int(frame))
    visual_frame = max(1, f - 1)
    active = f >= 8
    writes: list[tuple[int, int]] = []
    writes.extend(_timeline_5a_writes(f))
    writes.extend([
        (base + OFF_VISUAL_FRAME_A, visual_frame),
        (base + OFF_VISUAL_FRAME_B, visual_frame),
        (base + OFF_VISUAL_FRAME_C, visual_frame),
        (base + OFF_INPUT_SHADOW, 0x00000080),
    ])
    for off in VISUAL_SLOT_OFFSETS:
        writes.append((base + off, VISUAL_5A_SLOT_VALUE))
    if active:
        for off, value in ACTIVE_5A_STATIC_WRITES:
            writes.append((base + off, value))
    else:
        # First-frame input echo from the real 5A dump. This is not used as an
        # input spoof, it only mirrors the starter shadow while the visual
        # controller is being primed.
        writes.extend([
            (base + 0x13C8, 0x00000880),
            (base + 0x13CC, 0x00000800),
            (base + 0x13D0, 0x00000000),
            (base + 0x13D4, 0x00000080),
            (base + 0x13D8, 0x00000080),
        ])
    return writes


def _worker_visual_5a(frames: int) -> None:
    label = "5A visual controller bundle"
    _CANCEL.clear()
    _set_state(active=True, frames_requested=int(frames), frames_written=0, last_ok=False, last_error="", last_action=f"Running {label}.", started_at=_now(), finished_at=0.0)
    ok_total = total = 0
    try:
        print(f"\n[action force] ===== {label} START =====", flush=True)
        print("[action force] route=action bundle + visual controller + 0x0616 animation slots", flush=True)
        print(f"[action force] initial {_fmt_watch(_watch_values())}", flush=True)
        frame_deadline = _now()
        for frame in range(1, max(1, int(frames)) + 1):
            if _CANCEL.is_set():
                break
            natural_frame = frame + 1
            writes = _request_writes(MOVE_5A, include_echo=True) + _visual_controller_5a_writes(natural_frame)
            ok, count = _write_many(writes)
            ok_total += ok
            total += count
            if frame in {1, 2, 4, 8, 12, 15, 20, int(frames)}:
                print(f"[action force] f={frame:03d} natural={natural_frame:03d} {_fmt_watch(_watch_values())}", flush=True)
            _set_state(frames_written=frame, last_action=f"{label}: frame {frame}/{frames}")
            frame_deadline += _FRAME_SECONDS
            delay = frame_deadline - _now()
            if delay > 0:
                time.sleep(min(delay, _FRAME_SECONDS))
        print(f"[action force] final {_fmt_watch(_watch_values())}", flush=True)
        print(f"[action force] writes_ok={ok_total}/{total}", flush=True)
        _set_state(active=False, last_ok=(ok_total > 0 and ok_total == total), last_action=f"{label} done. writes_ok={ok_total}/{total}", finished_at=_now())
        print(f"[action force] ===== {label} END =====\n", flush=True)
    except Exception as e:
        _set_state(active=False, last_ok=False, last_error=f"{label} failed: {e!r}", finished_at=_now())
        print(f"[action force] ERROR {label}: {e!r}", flush=True)


def _worker_request(move_id: int, frames: int, include_echo: bool, label: str) -> None:
    _CANCEL.clear()
    _set_state(active=True, frames_requested=int(frames), frames_written=0, last_ok=False, last_error="", last_action=f"Running {label}.", started_at=_now(), finished_at=0.0)
    ok_total = total = 0
    try:
        print(f"\n[action force] ===== {label} START =====", flush=True)
        print(f"[action force] move_id=0x{int(move_id) & 0xFFFFFFFF:08X} route=request candidates only include_echo={include_echo}", flush=True)
        print(f"[action force] initial {_fmt_watch(_watch_values())}", flush=True)
        frame_deadline = _now()
        for frame in range(1, max(1, int(frames)) + 1):
            if _CANCEL.is_set():
                break
            ok, count = _write_many(_request_writes(move_id, include_echo=include_echo))
            ok_total += ok
            total += count
            if frame in {1, 2, 4, 8, 15, 30, int(frames)}:
                print(f"[action force] f={frame:03d} {_fmt_watch(_watch_values())}", flush=True)
            _set_state(frames_written=frame, last_action=f"{label}: frame {frame}/{frames}")
            frame_deadline += _FRAME_SECONDS
            delay = frame_deadline - _now()
            if delay > 0:
                time.sleep(min(delay, _FRAME_SECONDS))
        print(f"[action force] final {_fmt_watch(_watch_values())}", flush=True)
        print(f"[action force] writes_ok={ok_total}/{total}", flush=True)
        _set_state(active=False, last_ok=(ok_total > 0 and ok_total == total), last_action=f"{label} done. writes_ok={ok_total}/{total}", finished_at=_now())
        print(f"[action force] ===== {label} END =====\n", flush=True)
    except Exception as e:
        _set_state(active=False, last_ok=False, last_error=f"{label} failed: {e!r}", finished_at=_now())
        print(f"[action force] ERROR {label}: {e!r}", flush=True)


def _worker_bundle_once(move_id: int, label: str) -> None:
    _CANCEL.clear()
    _set_state(active=True, frames_requested=1, frames_written=0, last_ok=False, last_error="", last_action=f"Running {label}.", started_at=_now(), finished_at=0.0)
    ok_total = total = 0
    try:
        print(f"\n[action force] ===== {label} START =====", flush=True)
        print(f"[action force] move_id=0x{int(move_id) & 0xFFFFFFFF:08X} route=start bundle once + request candidates", flush=True)
        print(f"[action force] initial {_fmt_watch(_watch_values())}", flush=True)
        writes = _request_writes(move_id, include_echo=True) + _starter_bundle_writes(move_id)
        ok, count = _write_many(writes)
        ok_total += ok
        total += count
        print(f"[action force] after kick {_fmt_watch(_watch_values())}", flush=True)
        for n in range(1, 31):
            time.sleep(_FRAME_SECONDS)
            if n in {1, 2, 4, 8, 15, 30}:
                print(f"[action force] watch+{n:02d} {_fmt_watch(_watch_values())}", flush=True)
        print(f"[action force] final {_fmt_watch(_watch_values())}", flush=True)
        print(f"[action force] writes_ok={ok_total}/{total}", flush=True)
        _set_state(active=False, frames_written=1, last_ok=(ok_total > 0 and ok_total == total), last_action=f"{label} done. writes_ok={ok_total}/{total}", finished_at=_now())
        print(f"[action force] ===== {label} END =====\n", flush=True)
    except Exception as e:
        _set_state(active=False, last_ok=False, last_error=f"{label} failed: {e!r}", finished_at=_now())
        print(f"[action force] ERROR {label}: {e!r}", flush=True)


def _worker_timeline_5a(frames: int) -> None:
    label = "5A timeline bundle"
    _CANCEL.clear()
    _set_state(active=True, frames_requested=int(frames), frames_written=0, last_ok=False, last_error="", last_action=f"Running {label}.", started_at=_now(), finished_at=0.0)
    ok_total = total = 0
    try:
        print(f"\n[action force] ===== {label} START =====", flush=True)
        print("[action force] route=force natural 5A action bundle with frame counter", flush=True)
        print(f"[action force] initial {_fmt_watch(_watch_values())}", flush=True)
        frame_deadline = _now()
        for frame in range(1, max(1, int(frames)) + 1):
            if _CANCEL.is_set():
                break
            natural_frame = frame + 1
            writes = _request_writes(MOVE_5A, include_echo=True) + _timeline_5a_writes(natural_frame)
            ok, count = _write_many(writes)
            ok_total += ok
            total += count
            if frame in {1, 2, 4, 8, 12, 15, 20, int(frames)}:
                print(f"[action force] f={frame:03d} natural={natural_frame:03d} {_fmt_watch(_watch_values())}", flush=True)
            _set_state(frames_written=frame, last_action=f"{label}: frame {frame}/{frames}")
            frame_deadline += _FRAME_SECONDS
            delay = frame_deadline - _now()
            if delay > 0:
                time.sleep(min(delay, _FRAME_SECONDS))
        print(f"[action force] final {_fmt_watch(_watch_values())}", flush=True)
        print(f"[action force] writes_ok={ok_total}/{total}", flush=True)
        _set_state(active=False, last_ok=(ok_total > 0 and ok_total == total), last_action=f"{label} done. writes_ok={ok_total}/{total}", finished_at=_now())
        print(f"[action force] ===== {label} END =====\n", flush=True)
    except Exception as e:
        _set_state(active=False, last_ok=False, last_error=f"{label} failed: {e!r}", finished_at=_now())
        print(f"[action force] ERROR {label}: {e!r}", flush=True)


def _start_worker(target: Any, *args: Any) -> dict[str, Any]:
    global _WORKER
    with _LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            _STATE["last_action"] = "Action force already running."
            return dict(_STATE)
        thread = threading.Thread(target=target, args=args, daemon=True)
        _WORKER = thread
        thread.start()
        _STATE["last_action"] = "Queued action force."
        return dict(_STATE)


def request_5a_visual_controller() -> dict[str, Any]:
    return _start_worker(_worker_visual_5a, 24)


def request_5a_kick() -> dict[str, Any]:
    return _start_worker(_worker_bundle_once, MOVE_5A, "5A kick bundle once")


def request_5a_request() -> dict[str, Any]:
    return _start_worker(_worker_request, MOVE_5A, 12, False, "5A request only")


def request_5a_request_echo() -> dict[str, Any]:
    return _start_worker(_worker_request, MOVE_5A, 12, True, "5A request + echo")


def request_5a_timeline() -> dict[str, Any]:
    return _start_worker(_worker_timeline_5a, 24)


def request_5b_request() -> dict[str, Any]:
    return _start_worker(_worker_request, MOVE_5B, 12, True, "5B request + echo")


def request_hado_request() -> dict[str, Any]:
    return _start_worker(_worker_request, MOVE_HADO, 12, True, "Hado request + echo")


def cancel_action_force() -> dict[str, Any]:
    _CANCEL.set()
    _set_state(last_action="Cancel requested.")
    return get_action_force_state()
