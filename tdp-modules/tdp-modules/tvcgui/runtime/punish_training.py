from __future__ import annotations

import json
import os
import struct
import time
from typing import Any

try:
    from tvcgui.core.paths import user_data_path
except Exception:
    def user_data_path(*parts: str) -> str:
        base = os.path.join(os.path.expanduser("~"), "TvCGUI")
        return os.path.join(base, *parts)

try:
    from tvcgui.platform.dolphin import addr_in_ram, rd32, wd32, rbytes, wbytes
except Exception:
    addr_in_ram = None
    rd32 = None
    wd32 = None
    rbytes = None
    wbytes = None

try:
    import tvcgui.platform.patch_manager as runtime_pm
except Exception:
    runtime_pm = None

PUNISH_TRAINER_CONFIG_FILE = user_data_path("training", "punish_trainer.json")

PUNISH_TARGET_SLOT_PTRS = {
    "P1": 0x803C9FCC,
    "P2": 0x803C9FD4,
    "P1-C1": 0x803C9FCC,
    "P2-C1": 0x803C9FD4,
    "P1-C2": 0x803C9FDC,
    "P2-C2": 0x803C9FE4,
}

PUNISH_LABEL_ACTIONS = {
    "5A": 0x100,
    "5B": 0x101,
    "5C": 0x102,
    "2A": 0x103,
    "2B": 0x104,
    "2C": 0x105,
}
PUNISH_NORMAL_LABELS = tuple(PUNISH_LABEL_ACTIONS.keys())

PUNISH_DEFAULT_ENABLED = False
PUNISH_DEFAULT_TARGET_SIDE = "P2"
PUNISH_DEFAULT_RESPONSE_LABEL = "5A"
PUNISH_DEFAULT_MODE = "interval"
PUNISH_DEFAULT_INTERVAL_SEC = 3.0
PUNISH_DEFAULT_COOLDOWN_SEC = 0.25

# Names imported by main.py and used by the Punish Trainer window.
PUNISH_TRAINING_DEFAULT_MODE = "interval"
PUNISH_TRAINING_DEFAULT_OPPONENT_TEAM = "P2"
PUNISH_TRAINING_DEFAULT_INTERVAL_SEC = 3.0
PUNISH_TRAINING_DEFAULT_RELEASE_DELAY_FRAMES = 0
PUNISH_TRAINING_DEFAULT_RESPONSE_LABEL = "5A"

PUNISH_DIRECT_HOOK_ADDR = 0x80049078
PUNISH_DIRECT_HOOK_ORIGINAL = 0x4BFFF1F9
PUNISH_DIRECT_CAVE_ADDR = 0x812A5500
PUNISH_DIRECT_MAILBOX_ADDR = 0x812A55C0
PUNISH_DIRECT_CAVE_SIZE = 0x80
PUNISH_RETIRED_CALLSITE_ADDR = 0x80054A78
PUNISH_RETIRED_CALLSITE_ORIGINAL = 0x4BFF45ED

PUNISH_SELECTOR_HOOK_ADDR = 0x80049078
PUNISH_SELECTOR_ORIGINAL = 0x4BFFF1F9
PUNISH_SELECTOR_OLD_PATCH = 0x4925BF09
PUNISH_SELECTOR_OLD_CAVE_ADDR = 0x812A4F80
PUNISH_SELECTOR_OLD_MAILBOX_ADDR = 0x812A4FE0

MAIL_ENABLED = 0x00
MAIL_TARGET_BASE = 0x04
MAIL_ACTION_ID = 0x08
MAIL_HIT_COUNT = 0x0C
MAIL_LAST_BASE = 0x10
MAIL_LAST_PATH = 0x14
MAIL_LAST_OLD_ACTION = 0x20

_LAST = {
    "installed": False,
    "last_error": "",
    "last_trigger": "",
    "last_readback": {},
    "next_interval_time": 0.0,
    "prev_reaction": {},
    "last_fire_time": 0.0,
}

# Reaction families used only for the release trigger. They are intentionally broad.
PUNISH_REACTION_STATES = {
    48, 49, 50, 51, 52, 53, 60, 61, 62, 64, 65, 66, 73, 74, 75, 76,
    79, 80, 81, 82, 83, 88, 89, 90, 91, 92, 94, 95, 96, 97, 98,
    101, 102, 105, 106, 142, 449,
    4562, 4565, 4568, 4571, 4573, 4608, 4609, 4610, 4611, 4613, 4614,
    4615, 4616, 4617, 4618, 4619, 4620, 4621, 4622, 4623, 4625, 4631,
}


def _u32_bytes(value: int) -> bytes:
    return struct.pack(">I", int(value) & 0xFFFFFFFF)


def _read_u32(addr: int) -> int | None:
    if rd32 is None:
        return None
    try:
        val = rd32(int(addr))
    except Exception:
        return None
    if val is None:
        return None
    return int(val) & 0xFFFFFFFF


def _write_bytes(addr: int, payload: bytes, *, key: str, force: bool = True) -> bool:
    data = bytes(payload)
    if runtime_pm is not None:
        try:
            return bool(runtime_pm.write_bytes(int(addr), data, key=key, dirty=False, force=force))
        except Exception:
            pass
    if wbytes is None:
        return False
    try:
        return bool(wbytes(int(addr), data))
    except Exception:
        return False


def _write_u32(addr: int, value: int, *, key: str, force: bool = True) -> bool:
    if runtime_pm is not None:
        try:
            return bool(runtime_pm.write_u32(int(addr), int(value), key=key, dirty=False, force=force))
        except Exception:
            pass
    if wd32 is None:
        return False
    try:
        wd32(int(addr), int(value) & 0xFFFFFFFF)
        return True
    except Exception:
        return False


def _ppc_bl(src: int, dst: int) -> int:
    off = int(dst) - int(src)
    if off < -0x02000000 or off > 0x01FFFFFC or (off & 0x3):
        raise ValueError(f"branch out of range 0x{src:08X}->0x{dst:08X}")
    return 0x48000000 | (off & 0x03FFFFFC) | 1


def _build_direct_cave() -> bytes:
    # This hook is deliberately inside 0x80049064, replacing the call to
    # 0x80048270. The cave still calls the real resolver first, then optionally
    # replaces r3 with the requested normal. The rest of 0x80049064 then runs
    # untouched: store to +0x1E8, clear +0x5C, update +0x60, call 0x80046F24.
    words = [
        0x9421FFE0,  # stwu r1,-0x20(r1)
        0x7C0802A6,  # mflr r0
        0x90010024,  # stw r0,0x24(r1)
        0x60000000,  # bl 0x80048270, filled below
        0x3D80812A,  # lis r12,0x812A
        0x618C55C0,  # ori r12,r12,0x55C0
        0x906C0020,  # stw r3,0x20(r12), original resolver output
        0x93EC0010,  # stw r31,0x10(r12), last fighter base seen here
        0x816C0000,  # lwz r11,0(r12), enabled
        0x2C0B0000,  # cmpwi r11,0
        0x4182003C,  # beq done
        0x816C0004,  # lwz r11,4(r12), target base
        0x7C1F5800,  # cmpw r31,r11
        0x40820030,  # bne done
        0x816C0008,  # lwz r11,8(r12), forced action
        0x2C0B0000,  # cmpwi r11,0
        0x41820024,  # beq done
        0x7D635B78,  # mr r3,r11
        0x39600000,  # li r11,0
        0x916C0000,  # stw r11,0(r12), consume
        0x816C000C,  # lwz r11,0x0C(r12), hit count
        0x396B0001,  # addi r11,r11,1
        0x916C000C,  # stw r11,0x0C(r12)
        0x39600002,  # li r11,2
        0x916C0014,  # stw r11,0x14(r12), last path consumed
        0x80010024,  # done: lwz r0,0x24(r1)
        0x7C0803A6,  # mtlr r0
        0x38210020,  # addi r1,r1,0x20
        0x4E800020,  # blr
    ]
    words[3] = _ppc_bl(PUNISH_DIRECT_CAVE_ADDR + 0x0C, 0x80048270)
    return b"".join(_u32_bytes(w) for w in words)


def _zero_direct_mailbox(keep_hits: bool = True) -> bool:
    hit_count = _read_u32(PUNISH_DIRECT_MAILBOX_ADDR + MAIL_HIT_COUNT) if keep_hits else 0
    data = bytearray(0x40)
    if keep_hits and hit_count is not None:
        data[MAIL_HIT_COUNT:MAIL_HIT_COUNT + 4] = _u32_bytes(hit_count)
    return _write_bytes(PUNISH_DIRECT_MAILBOX_ADDR, bytes(data), key="punish:mailbox:zero")


def punish_readback() -> dict[str, Any]:
    cave = []
    if rbytes is not None:
        try:
            raw = rbytes(PUNISH_DIRECT_CAVE_ADDR, 0x20) or b""
            for i in range(0, min(len(raw), 0x20), 4):
                cave.append(struct.unpack_from(">I", raw, i)[0])
        except Exception:
            cave = []
    data = {
        "direct_site": _read_u32(PUNISH_DIRECT_HOOK_ADDR),
        "direct_site_expected": _ppc_bl(PUNISH_DIRECT_HOOK_ADDR, PUNISH_DIRECT_CAVE_ADDR),
        "direct_original": PUNISH_DIRECT_HOOK_ORIGINAL,
        "selector_site": _read_u32(PUNISH_SELECTOR_HOOK_ADDR),
        "selector_original": PUNISH_SELECTOR_ORIGINAL,
        "selector_old_patch": PUNISH_SELECTOR_OLD_PATCH,
        "cave_first_words": cave,
        "mail_enabled": _read_u32(PUNISH_DIRECT_MAILBOX_ADDR + MAIL_ENABLED),
        "mail_target": _read_u32(PUNISH_DIRECT_MAILBOX_ADDR + MAIL_TARGET_BASE),
        "mail_action": _read_u32(PUNISH_DIRECT_MAILBOX_ADDR + MAIL_ACTION_ID),
        "mail_hits": _read_u32(PUNISH_DIRECT_MAILBOX_ADDR + MAIL_HIT_COUNT),
        "mail_last_base": _read_u32(PUNISH_DIRECT_MAILBOX_ADDR + MAIL_LAST_BASE),
        "mail_last_path": _read_u32(PUNISH_DIRECT_MAILBOX_ADDR + MAIL_LAST_PATH),
        "old_selector_enabled": _read_u32(PUNISH_SELECTOR_OLD_MAILBOX_ADDR),
        "old_selector_target": _read_u32(PUNISH_SELECTOR_OLD_MAILBOX_ADDR + 4),
        "old_selector_action": _read_u32(PUNISH_SELECTOR_OLD_MAILBOX_ADDR + 8),
    }
    _LAST["last_readback"] = data
    return data


def _fmt_u32(value: Any) -> str:
    if value is None:
        return "None"
    try:
        return f"0x{int(value) & 0xFFFFFFFF:08X}"
    except Exception:
        return str(value)


def print_punish_readback(prefix: str = "[punish trainer]") -> dict[str, Any]:
    rb = punish_readback()
    try:
        cave_s = " ".join(_fmt_u32(v) for v in rb.get("cave_first_words", [])[:8])
        print(
            f"{prefix} readback direct@0x{PUNISH_DIRECT_HOOK_ADDR:08X}={_fmt_u32(rb.get('direct_site'))} "
            f"expected={_fmt_u32(rb.get('direct_site_expected'))} original={_fmt_u32(rb.get('direct_original'))}; "
            f"selector@0x{PUNISH_SELECTOR_HOOK_ADDR:08X}={_fmt_u32(rb.get('selector_site'))} "
            f"orig={_fmt_u32(rb.get('selector_original'))} old={_fmt_u32(rb.get('selector_old_patch'))}; "
            f"cave={cave_s}",
            flush=True,
        )
        print(
            f"{prefix} mailbox enabled={_fmt_u32(rb.get('mail_enabled'))} "
            f"target={_fmt_u32(rb.get('mail_target'))} action={_fmt_u32(rb.get('mail_action'))} "
            f"hits={_fmt_u32(rb.get('mail_hits'))} last_base={_fmt_u32(rb.get('mail_last_base'))} "
            f"last_path={_fmt_u32(rb.get('mail_last_path'))}; "
            f"old_selector_enabled={_fmt_u32(rb.get('old_selector_enabled'))}",
            flush=True,
        )
    except Exception:
        pass
    return rb


def install_punish_direct_action_hook(*, restore_stale_selector: bool = True, verbose: bool = True) -> bool:
    expected_hook = _ppc_bl(PUNISH_DIRECT_HOOK_ADDR, PUNISH_DIRECT_CAVE_ADDR)
    cave = _build_direct_cave()
    ok = True

    # Clean up the abandoned v21/v22 per-frame callsite hook if it is present.
    retired_site = _read_u32(PUNISH_RETIRED_CALLSITE_ADDR)
    if retired_site is not None and retired_site != PUNISH_RETIRED_CALLSITE_ORIGINAL:
        ok = _write_u32(PUNISH_RETIRED_CALLSITE_ADDR, PUNISH_RETIRED_CALLSITE_ORIGINAL, key="punish:restore-retired-callsite") and ok

    # Clear the retired v20 mailbox. The new hook owns 0x80049078 and uses a
    # new mailbox so stale enabled/action values cannot fire unexpectedly.
    if restore_stale_selector:
        _write_bytes(PUNISH_SELECTOR_OLD_MAILBOX_ADDR, b"\x00" * 0x20, key="punish:old-selector-mailbox")

    ok = _write_bytes(PUNISH_DIRECT_CAVE_ADDR, cave, key="punish:resolver-cave") and ok
    _zero_direct_mailbox(keep_hits=True)
    ok = _write_u32(PUNISH_DIRECT_HOOK_ADDR, expected_hook, key="punish:resolver-hook") and ok

    rb = punish_readback()
    installed = bool(
        ok
        and rb.get("direct_site") == expected_hook
        and (rb.get("cave_first_words") or [None])[0] == 0x9421FFE0
    )
    _LAST["installed"] = installed
    _LAST["last_error"] = "" if installed else "resolver hook readback failed"
    if verbose:
        print_punish_readback()
        if installed:
            print("[punish trainer] resolver action hook installed", flush=True)
        else:
            print("[punish trainer] resolver action hook install failed", flush=True)
    return installed


def uninstall_punish_direct_action_hook(verbose: bool = True) -> bool:
    ok = _write_u32(PUNISH_DIRECT_HOOK_ADDR, PUNISH_DIRECT_HOOK_ORIGINAL, key="punish:direct-unhook")
    _zero_direct_mailbox(keep_hits=False)
    _LAST["installed"] = False
    if verbose:
        print_punish_readback()
    return bool(ok)


def punish_label_to_action(label: Any) -> int | None:
    text = str(label or "").strip().upper().replace(" ", "")
    text = text.replace("J.", "J")
    if not text:
        return None
    if text in PUNISH_LABEL_ACTIONS:
        return int(PUNISH_LABEL_ACTIONS[text])
    try:
        if text.startswith("0X"):
            return int(text, 16) & 0xFFFF
        return int(text, 10) & 0xFFFF
    except Exception:
        return None


def _snap_base(snap: Any) -> int | None:
    if not isinstance(snap, dict):
        return None
    for key in ("base", "fighter_base", "ea"):
        try:
            base = int(snap.get(key) or 0)
        except Exception:
            base = 0
        if base and (addr_in_ram is None or bool(addr_in_ram(base))):
            return base
    return None


def punish_target_base(target_side: str = PUNISH_DEFAULT_TARGET_SIDE, snaps: dict | None = None) -> int | None:
    side = str(target_side or PUNISH_DEFAULT_TARGET_SIDE).strip().upper()
    if side in {"1", "PLAYER1"}:
        side = "P1"
    elif side in {"2", "PLAYER2", "CPU"}:
        side = "P2"
    if snaps:
        for key in (side, f"{side}-C1"):
            base = _snap_base(snaps.get(key))
            if base:
                return base
    ptr = PUNISH_TARGET_SLOT_PTRS.get(side) or PUNISH_TARGET_SLOT_PTRS.get(f"{side}-C1")
    if ptr is None:
        return None
    base = _read_u32(ptr)
    if base and (addr_in_ram is None or bool(addr_in_ram(base))):
        return int(base)
    return None


def arm_punish_direct_action(target_base: int, action_id: int, *, reason: str = "manual", verbose: bool = True) -> bool:
    try:
        base = int(target_base) & 0xFFFFFFFF
        action = int(action_id) & 0xFFFF
    except Exception as e:
        _LAST["last_error"] = f"bad trigger args: {e!r}"
        return False
    if not base or not action:
        _LAST["last_error"] = f"bad trigger base/action base=0x{base:08X} action=0x{action:04X}"
        return False
    if not _LAST.get("installed"):
        if not install_punish_direct_action_hook(verbose=verbose):
            return False

    # Write target and action first, then arm enabled last so the cave cannot
    # observe a half-written packet.
    ok = True
    ok = _write_u32(PUNISH_DIRECT_MAILBOX_ADDR + MAIL_TARGET_BASE, base, key="punish:mail-target") and ok
    ok = _write_u32(PUNISH_DIRECT_MAILBOX_ADDR + MAIL_ACTION_ID, action, key="punish:mail-action") and ok
    ok = _write_u32(PUNISH_DIRECT_MAILBOX_ADDR + MAIL_LAST_PATH, 0, key="punish:mail-path") and ok
    ok = _write_u32(PUNISH_DIRECT_MAILBOX_ADDR + MAIL_ENABLED, 1, key="punish:mail-enable") and ok
    _LAST["last_trigger"] = f"{reason}: base=0x{base:08X} action=0x{action:04X}"
    if verbose:
        print(f"[punish trainer] armed direct action {reason}: base=0x{base:08X} action=0x{action:04X}", flush=True)
        print_punish_readback()
    return bool(ok)


def trigger_punish_direct_action(target_side: str = PUNISH_DEFAULT_TARGET_SIDE, label: str = PUNISH_DEFAULT_RESPONSE_LABEL, snaps: dict | None = None, *, verbose: bool = True) -> bool:
    base = punish_target_base(target_side, snaps)
    action = punish_label_to_action(label)
    if base is None:
        _LAST["last_error"] = f"no live target base for {target_side}"
        if verbose:
            print(f"[punish trainer] {_LAST['last_error']}", flush=True)
        return False
    if action is None:
        _LAST["last_error"] = f"unknown response label {label!r}"
        if verbose:
            print(f"[punish trainer] {_LAST['last_error']}", flush=True)
        return False
    return arm_punish_direct_action(base, action, reason=f"{target_side} {label}", verbose=verbose)


def punish_direct_action_consumed() -> bool:
    return _read_u32(PUNISH_DIRECT_MAILBOX_ADDR + MAIL_ENABLED) == 0 and _read_u32(PUNISH_DIRECT_MAILBOX_ADDR + MAIL_LAST_PATH) == 2


def get_punish_training_debug_state() -> dict[str, Any]:
    rb = punish_readback()
    return {
        "installed": bool(_LAST.get("installed")),
        "last_error": str(_LAST.get("last_error") or ""),
        "last_trigger": str(_LAST.get("last_trigger") or ""),
        "readback": rb,
    }


def _clean_label(value: Any) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    while "  " in text:
        text = text.replace("  ", " ")
    return text[:96]


def _normalize_response_label(value: Any, *, allow_blank: bool = True) -> str:
    label = _clean_label(value).upper().replace(" ", "")
    if label in {"CHOOSEMOVE", "ANY", "NONE"}:
        label = ""
    if not label:
        return "" if allow_blank else PUNISH_TRAINING_DEFAULT_RESPONSE_LABEL
    if punish_label_to_action(label) is None:
        return "" if allow_blank else PUNISH_TRAINING_DEFAULT_RESPONSE_LABEL
    return label


def _clamp_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        out = float(value)
    except Exception:
        out = float(default)
    return round(max(float(low), min(float(high), out)), 3)


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        out = int(round(float(value)))
    except Exception:
        out = int(default)
    return max(int(low), min(int(high), out))


def _normalize_punish_training_state(state: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(state or {})

    mode = str(raw.get("mode") or PUNISH_TRAINING_DEFAULT_MODE).strip().lower()
    if mode in {"repeat", "timer", "timed"}:
        mode = "interval"
    elif mode in {"release", "after_blockstun", "after_stun_release", "stun_release"}:
        mode = "after_stun"
    if mode not in {"interval", "after_stun"}:
        mode = PUNISH_TRAINING_DEFAULT_MODE

    team = str(raw.get("opponent_team") or raw.get("target_side") or raw.get("target") or PUNISH_TRAINING_DEFAULT_OPPONENT_TEAM).strip().upper()
    if team in {"1", "PLAYER1"}:
        team = "P1"
    elif team in {"2", "PLAYER2", "CPU"}:
        team = "P2"
    if team not in {"P1", "P2"}:
        team = PUNISH_TRAINING_DEFAULT_OPPONENT_TEAM

    interval = _clamp_float(raw.get("interval_sec"), PUNISH_TRAINING_DEFAULT_INTERVAL_SEC, 0.25, 60.0)
    release_delay = _clamp_int(raw.get("release_delay_frames"), PUNISH_TRAINING_DEFAULT_RELEASE_DELAY_FRAMES, 0, 300)

    out = {
        "enabled": bool(raw.get("enabled", PUNISH_DEFAULT_ENABLED)),
        "mode": mode,
        "opponent_team": team,
        "target_side": team,
        "response_label": _normalize_response_label(raw.get("response_label") or raw.get("label"), allow_blank=True),
        "source_label": _clean_label(raw.get("source_label") or ""),
        "interval_sec": interval,
        "release_delay_frames": release_delay,
        "pulses": raw.get("pulses") if isinstance(raw.get("pulses"), dict) else {},
        "scheduled": raw.get("scheduled") if isinstance(raw.get("scheduled"), dict) else None,
        "release_watch": raw.get("release_watch") if isinstance(raw.get("release_watch"), dict) else {},
        "next_interval_at": _clamp_float(raw.get("next_interval_at"), 0.0, 0.0, 999999999.0),
        "last_trigger": raw.get("last_trigger"),
        "last_status": str(raw.get("last_status") or ""),
        "trigger_count": _clamp_int(raw.get("trigger_count"), 0, 0, 999999),
    }
    if raw.get("manual_test_requested"):
        out["manual_test_requested"] = True
        out["manual_test_requested_at"] = raw.get("manual_test_requested_at")
    return out


def _load_punish_training_config() -> dict[str, Any]:
    state = {
        "enabled": PUNISH_DEFAULT_ENABLED,
        "mode": PUNISH_TRAINING_DEFAULT_MODE,
        "opponent_team": PUNISH_TRAINING_DEFAULT_OPPONENT_TEAM,
        "target_side": PUNISH_TRAINING_DEFAULT_OPPONENT_TEAM,
        "response_label": "",
        "source_label": "",
        "interval_sec": PUNISH_TRAINING_DEFAULT_INTERVAL_SEC,
        "release_delay_frames": PUNISH_TRAINING_DEFAULT_RELEASE_DELAY_FRAMES,
        "pulses": {},
        "scheduled": None,
        "release_watch": {},
        "next_interval_at": 0.0,
        "last_trigger": None,
        "last_status": "",
        "trigger_count": 0,
    }
    try:
        with open(PUNISH_TRAINER_CONFIG_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            state.update(loaded)
    except Exception:
        pass
    return _normalize_punish_training_state(state)


def _save_punish_training_config(state: dict[str, Any]) -> None:
    try:
        path = PUNISH_TRAINER_CONFIG_FILE
        os.makedirs(os.path.dirname(path), exist_ok=True)
        clean = _normalize_punish_training_state(state)
        # Runtime-only fields should not make the saved config noisy.
        for key in ("pulses", "scheduled", "release_watch", "last_trigger", "last_status", "manual_test_requested", "manual_test_requested_at"):
            clean.pop(key, None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2, sort_keys=True)
    except Exception as e:
        print(f"[punish trainer] config save failed: {e!r}", flush=True)


def _clear_punish_training_runtime(state: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(state, dict):
        state["pulses"] = {}
        state["scheduled"] = None
        state["release_watch"] = {}
        state["next_interval_at"] = 0.0
        state["manual_test_requested"] = False
        state["last_status"] = ""
    try:
        _zero_direct_mailbox(keep_hits=True)
    except Exception:
        pass
    _LAST["next_interval_time"] = 0.0
    _LAST["prev_reaction"] = {}
    return state if isinstance(state, dict) else {}


def _snap_label_text(snap: Any) -> str:
    if not isinstance(snap, dict):
        return ""
    parts = []
    for key in ("move_label", "label", "move", "name", "display", "attack_label", "att_label"):
        val = snap.get(key)
        if val:
            parts.append(str(val))
    return " ".join(parts)


def _label_matches_snap(label: str, snap: Any) -> bool:
    label = _clean_label(label)
    if not label:
        return True
    want = "".join(ch for ch in label.casefold() if ch.isalnum())
    if not want:
        return True
    have = "".join(ch for ch in _snap_label_text(snap).casefold() if ch.isalnum())
    if want and have and want in have:
        return True
    action = punish_label_to_action(label)
    if action is None or not isinstance(snap, dict):
        return False
    for key in ("action", "action_id", "move_id", "att_id", "id"):
        try:
            if int(snap.get(key)) == int(action):
                return True
        except Exception:
            pass
    return False


def _punish_training_roster_context(snaps: dict | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for team in ("P1", "P2"):
        snap = (snaps or {}).get(f"{team}-C1") or (snaps or {}).get(team) or {}
        base = _snap_base(snap) or punish_target_base(team, snaps)
        name = ""
        if isinstance(snap, dict):
            name = str(snap.get("name") or snap.get("char_name") or snap.get("character") or "")
        out[team] = {
            "team": team,
            "side": team,
            "slot": f"{team}-C1",
            "slot_label": f"{team}-C1",
            "base": base,
            "name": name,
            "labels": list(PUNISH_NORMAL_LABELS),
        }
    return out


def _punish_training_mode_summary(state: dict[str, Any] | None) -> str:
    s = _normalize_punish_training_state(state)
    label = s.get("response_label") or "choose move"
    team = s.get("opponent_team") or PUNISH_TRAINING_DEFAULT_OPPONENT_TEAM
    if s.get("mode") == "after_stun":
        delay = int(s.get("release_delay_frames") or 0)
        return f"{team} {label} after stun +{delay}f"
    return f"{team} {label} every {float(s.get('interval_sec') or PUNISH_TRAINING_DEFAULT_INTERVAL_SEC):g}s"


def _punish_training_status(state: dict[str, Any] | None, now: float | None = None) -> str:
    if not isinstance(state, dict):
        return ""
    status = str(state.get("last_status") or "")
    if status:
        return status
    rb = _LAST.get("last_readback") or {}
    hits = rb.get("mail_hits")
    if hits not in (None, 0):
        try:
            return f"Punish hook hits {int(hits)}"
        except Exception:
            return ""
    return ""


def _set_status(state: dict[str, Any], text: str) -> None:
    state["last_status"] = str(text or "")[:160]


def _trigger_punish_training_now(state: dict[str, Any], snaps: dict | None, *, reason: str, verbose: bool = True) -> bool:
    label = str(state.get("response_label") or "")
    team = str(state.get("opponent_team") or PUNISH_TRAINING_DEFAULT_OPPONENT_TEAM)
    if not label:
        _set_status(state, "Punish: choose a response move")
        return False
    before_hits = _read_u32(PUNISH_DIRECT_MAILBOX_ADDR + MAIL_HIT_COUNT) or 0
    ok = trigger_punish_direct_action(team, label, snaps, verbose=verbose)
    after_hits = _read_u32(PUNISH_DIRECT_MAILBOX_ADDR + MAIL_HIT_COUNT) or before_hits
    if ok:
        state["trigger_count"] = int(state.get("trigger_count") or 0) + 1
        state["last_trigger"] = {
            "time": time.monotonic(),
            "team": team,
            "label": label,
            "reason": reason,
            "hook_hits_before": before_hits,
            "hook_hits_after": after_hits,
        }
        _set_status(state, f"Punish armed {team} {label} ({reason})")
    else:
        _set_status(state, f"Punish failed: {_LAST.get('last_error') or 'not armed'}")
    return ok


def _tick_punish_training(state: dict[str, Any] | None, snaps: dict | None = None, now: float | None = None, frame_idx: int | None = None) -> dict[str, Any]:
    s = _normalize_punish_training_state(state)
    now_f = float(now if now is not None else time.monotonic())
    frame_i = int(frame_idx or 0)

    if isinstance(state, dict):
        state.clear()
        state.update(s)
        s = state

    if s.pop("manual_test_requested", False):
        s["manual_test_requested"] = False
        _trigger_punish_training_now(s, snaps, reason="manual", verbose=True)
        return s

    if not bool(s.get("enabled", False)):
        return s

    mode = str(s.get("mode") or PUNISH_TRAINING_DEFAULT_MODE)
    if mode == "interval":
        due = float(s.get("next_interval_at") or 0.0)
        if now_f >= due:
            if _trigger_punish_training_now(s, snaps, reason="interval", verbose=True):
                s["next_interval_at"] = now_f + float(s.get("interval_sec") or PUNISH_TRAINING_DEFAULT_INTERVAL_SEC)
            else:
                s["next_interval_at"] = now_f + 0.5
        return s

    if mode == "after_stun":
        target = str(s.get("opponent_team") or PUNISH_TRAINING_DEFAULT_OPPONENT_TEAM)
        source_team = "P2" if target == "P1" else "P1"
        source_snap = (snaps or {}).get(f"{source_team}-C1") or (snaps or {}).get(source_team) or {}
        release_watch = s.setdefault("release_watch", {})
        in_reaction = _is_reaction_snap(source_snap)
        label_ok = _label_matches_snap(str(s.get("source_label") or ""), source_snap)
        prior = bool(release_watch.get("in_reaction", False))
        prior_label = bool(release_watch.get("label_ok", False))
        release_watch["in_reaction"] = bool(in_reaction)
        release_watch["label_ok"] = bool(label_ok)

        if prior and not in_reaction and (prior_label or not s.get("source_label")):
            delay = int(s.get("release_delay_frames") or 0)
            if delay <= 0:
                _trigger_punish_training_now(s, snaps, reason="stun release", verbose=True)
            else:
                s["scheduled"] = {"fire_frame": frame_i + delay, "reason": "stun release"}

        scheduled = s.get("scheduled")
        if isinstance(scheduled, dict):
            fire_frame = int(scheduled.get("fire_frame") or 0)
            if frame_i >= fire_frame:
                s["scheduled"] = None
                _trigger_punish_training_now(s, snaps, reason=str(scheduled.get("reason") or "scheduled"), verbose=True)
        return s

    return s


# Compatibility aliases for older scratch builds and the public window/main imports.
def _load_punish_trainer_config() -> dict[str, Any]:
    return _load_punish_training_config()


def _save_punish_trainer_config(state: dict[str, Any]) -> None:
    _save_punish_training_config(state)


def _tick_punish_trainer(state: dict[str, Any] | None, snaps: dict | None = None, frame_idx: int | None = None) -> dict[str, Any]:
    return _tick_punish_training(state, snaps, time.monotonic(), frame_idx)


def _punish_roster_context(snaps: dict | None) -> dict[str, dict[str, Any]]:
    return _punish_training_roster_context(snaps)


def _sync_mission_punish_trainer(*_args: Any, **_kwargs: Any) -> None:
    return None


load_punish_trainer_config = _load_punish_training_config
save_punish_trainer_config = _save_punish_training_config
tick_punish_trainer = _tick_punish_trainer
punish_roster_context = _punish_training_roster_context
install_direct_action_hook = install_punish_direct_action_hook
trigger_direct_action = trigger_punish_direct_action
manual_test_punish_response = trigger_punish_direct_action
