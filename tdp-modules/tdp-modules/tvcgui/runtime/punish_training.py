from __future__ import annotations

import json
import os
import random
import re
import time
from typing import Any

try:
    from tvcgui.core.paths import user_data_path
except Exception:
    def user_data_path(*parts: str) -> str:
        return os.path.join(os.path.expanduser("~"), "TvCGUI", *parts)

try:
    from tvcgui.platform.dolphin import addr_in_ram, rd32, wd32
except Exception:
    addr_in_ram = None
    rd32 = None
    wd32 = None

PUNISH_TRAINER_CONFIG_FILE = user_data_path("training", "punish_trainer.json")

PUNISH_TARGET_SLOT_PTRS = {
    "P1-C1": 0x803C9FCC,
    "P2-C1": 0x803C9FD4,
    "P1-C2": 0x803C9FDC,
    "P2-C2": 0x803C9FE4,
}

OFF_ACTION_ID = 0x1E8
OFF_ACTION_REQUEST = 0x200

PUNISH_DEFAULT_SLOT = "P2-C1"
PUNISH_DEFAULT_COOLDOWN_SEC = 3.0
PUNISH_DEFAULT_RANDOM_MIN_SEC = 5.0
PUNISH_DEFAULT_RANDOM_MAX_SEC = 10.0
PUNISH_DEFAULT_MOVE_LABEL = "5A [0x100]"
PUNISH_DEFAULT_ACTION_ID = 0x100

PUNISH_LABEL_ACTIONS = {
    "5A": 0x100,
    "5B": 0x101,
    "5C": 0x102,
    "2A": 0x103,
    "2B": 0x104,
    "2C": 0x105,
}

_RUNTIME_KEYS = {
    "phase",
    "next_fire_at",
    "request_deadline",
    "request_attempts",
    "target_base",
    "last_request_addr",
    "last_mailbox_value",
    "last_seen_action",
    "manual_only",
    "last_status",
    "last_status_at",
    "last_trigger",
    "trigger_count",
    "scheduled_cooldown_sec",
    "countdown_flash_until",
}


def _now() -> float:
    return time.monotonic()


def _read_u32(addr: int, default: int = 0) -> int:
    if rd32 is None:
        return int(default) & 0xFFFFFFFF
    try:
        value = rd32(int(addr))
    except Exception:
        value = None
    if value is None:
        return int(default) & 0xFFFFFFFF
    return int(value) & 0xFFFFFFFF


def _write_u32(addr: int, value: int) -> bool:
    if wd32 is None:
        return False
    try:
        result = wd32(int(addr), int(value) & 0xFFFFFFFF)
        return result is not False
    except Exception:
        return False


def _valid_base(base: int) -> bool:
    base = int(base or 0)
    if not base:
        return False
    if addr_in_ram is not None:
        try:
            return bool(addr_in_ram(base))
        except Exception:
            pass
    return 0x90000000 <= base < 0x94000000


def _clamp_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        value = float(value)
    except Exception:
        value = default
    return round(max(low, min(high, value)), 2)


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        value = int(value)
    except Exception:
        value = default
    return max(low, min(high, value))


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:128]


def _mailbox_value_for_action(action_id: int) -> int:
    # The resolver adds 0x4000 before returning the action. Pre-adjust the
    # mailbox so the committed action is the requested action exactly.
    return (int(action_id) - 0x4000) & 0xFFFFFFFF


def parse_action_id(value: Any, fallback: int | None = None) -> int | None:
    if value is None:
        return fallback
    if isinstance(value, int):
        return int(value) & 0xFFFF
    text = _clean_text(value).upper()
    if not text:
        return fallback
    bracket = re.search(r"\[\s*0X([0-9A-F]+)\s*\]", text)
    if bracket:
        return int(bracket.group(1), 16) & 0xFFFF
    direct = re.search(r"0X([0-9A-F]+)", text)
    if direct:
        return int(direct.group(1), 16) & 0xFFFF
    compact = text.replace(" ", "")
    if compact in PUNISH_LABEL_ACTIONS:
        return PUNISH_LABEL_ACTIONS[compact]
    try:
        return int(compact, 10) & 0xFFFF
    except Exception:
        return fallback


def _normalize_slot(value: Any) -> str:
    text = str(value or PUNISH_DEFAULT_SLOT).strip().upper()
    aliases = {
        "P1": "P1-C1",
        "P2": "P2-C1",
        "1": "P1-C1",
        "2": "P2-C1",
        "P1C1": "P1-C1",
        "P1C2": "P1-C2",
        "P2C1": "P2-C1",
        "P2C2": "P2-C2",
    }
    text = aliases.get(text, text)
    return text if text in PUNISH_TARGET_SLOT_PTRS else PUNISH_DEFAULT_SLOT


def normalize_punish_trainer_state(state: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(state or {})
    slot = _normalize_slot(raw.get("target_slot") or raw.get("opponent_team") or raw.get("target_side"))
    move_label = _clean_text(raw.get("move_label") or raw.get("response_label") or PUNISH_DEFAULT_MOVE_LABEL)
    action_id = parse_action_id(raw.get("action_id"), None)
    if action_id is None:
        action_id = parse_action_id(move_label, PUNISH_DEFAULT_ACTION_ID)
    cooldown_sec = _clamp_float(
        raw.get("cooldown_sec", raw.get("interval_sec")),
        PUNISH_DEFAULT_COOLDOWN_SEC,
        0.25,
        30.0,
    )
    random_min_sec = _clamp_float(
        raw.get("random_min_sec", raw.get("interval_min_sec")),
        PUNISH_DEFAULT_RANDOM_MIN_SEC,
        0.25,
        30.0,
    )
    random_max_sec = _clamp_float(
        raw.get("random_max_sec", raw.get("interval_max_sec")),
        PUNISH_DEFAULT_RANDOM_MAX_SEC,
        0.25,
        30.0,
    )
    if random_min_sec > random_max_sec:
        random_min_sec, random_max_sec = random_max_sec, random_min_sec

    out = {
        "enabled": bool(raw.get("enabled", False)),
        "target_slot": slot,
        "move_label": move_label or PUNISH_DEFAULT_MOVE_LABEL,
        "action_id": int(action_id or PUNISH_DEFAULT_ACTION_ID) & 0xFFFF,
        "cooldown_sec": cooldown_sec,
        "random_interval": bool(raw.get("random_interval", False)),
        "random_min_sec": random_min_sec,
        "random_max_sec": random_max_sec,
        "show_countdown": bool(raw.get("show_countdown", False)),
        "manual_test_requested": bool(raw.get("manual_test_requested", False)),
    }
    for key in _RUNTIME_KEYS:
        if key in raw:
            out[key] = raw[key]
    out.setdefault("phase", "off")
    out.setdefault("next_fire_at", 0.0)
    out.setdefault("request_deadline", 0.0)
    out.setdefault("request_attempts", 0)
    out.setdefault("target_base", 0)
    out.setdefault("last_request_addr", 0)
    out.setdefault("last_mailbox_value", 0)
    out.setdefault("last_seen_action", 0)
    out.setdefault("manual_only", False)
    out.setdefault("last_status", "Punish Trainer off.")
    out.setdefault("last_status_at", 0.0)
    out.setdefault("last_trigger", None)
    out.setdefault("trigger_count", 0)
    out.setdefault("scheduled_cooldown_sec", 0.0)
    out.setdefault("countdown_flash_until", 0.0)
    return out


def load_punish_trainer_config() -> dict[str, Any]:
    loaded: dict[str, Any] = {}
    try:
        with open(PUNISH_TRAINER_CONFIG_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            loaded = data
    except Exception:
        pass
    return normalize_punish_trainer_state(loaded)


def save_punish_trainer_config(state: dict[str, Any]) -> None:
    clean = normalize_punish_trainer_state(state)
    for key in _RUNTIME_KEYS:
        clean.pop(key, None)
    clean.pop("manual_test_requested", None)
    try:
        os.makedirs(os.path.dirname(PUNISH_TRAINER_CONFIG_FILE), exist_ok=True)
        with open(PUNISH_TRAINER_CONFIG_FILE, "w", encoding="utf-8") as handle:
            json.dump(clean, handle, indent=2, sort_keys=True)
    except Exception as exc:
        print(f"[punish trainer] config save failed: {exc!r}", flush=True)


def _set_status(state: dict[str, Any], text: str, now: float | None = None) -> None:
    state["last_status"] = _clean_text(text)[:180]
    state["last_status_at"] = float(_now() if now is None else now)


def _snap_base(snap: Any) -> int:
    if not isinstance(snap, dict):
        return 0
    for key in ("base", "fighter_base", "ea"):
        try:
            base = int(snap.get(key) or 0)
        except Exception:
            base = 0
        if _valid_base(base):
            return base
    return 0


def punish_target_base(target_slot: str, snaps: dict | None = None) -> int:
    slot = _normalize_slot(target_slot)
    base = _snap_base((snaps or {}).get(slot))
    if base:
        return base
    ptr = PUNISH_TARGET_SLOT_PTRS[slot]
    base = _read_u32(ptr, 0)
    return base if _valid_base(base) else 0


def _clear_our_request(state: dict[str, Any]) -> None:
    addr = int(state.get("last_request_addr") or 0)
    value = int(state.get("last_mailbox_value") or 0) & 0xFFFFFFFF
    if addr and value and _read_u32(addr, 0) == value:
        _write_u32(addr, 0)
    state["last_request_addr"] = 0
    state["last_mailbox_value"] = 0


def clear_punish_trainer_runtime(state: dict[str, Any] | None = None) -> dict[str, Any]:
    target = normalize_punish_trainer_state(state)
    _clear_our_request(target)
    target.update(
        {
            "phase": "off",
            "next_fire_at": 0.0,
            "request_deadline": 0.0,
            "request_attempts": 0,
            "target_base": 0,
            "manual_only": False,
            "manual_test_requested": False,
            "scheduled_cooldown_sec": 0.0,
            "countdown_flash_until": 0.0,
        }
    )
    if isinstance(state, dict):
        state.clear()
        state.update(target)
        return state
    return target



def _selected_interval(state: dict[str, Any]) -> float:
    if bool(state.get("random_interval", False)):
        low = float(state.get("random_min_sec") or PUNISH_DEFAULT_RANDOM_MIN_SEC)
        high = float(state.get("random_max_sec") or PUNISH_DEFAULT_RANDOM_MAX_SEC)
        if low > high:
            low, high = high, low
        return round(random.uniform(low, high), 2)
    return float(state.get("cooldown_sec") or PUNISH_DEFAULT_COOLDOWN_SEC)


def _schedule_cooldown(state: dict[str, Any], now: float, *, initial: bool = False) -> float:
    if initial and not bool(state.get("random_interval", False)):
        duration = 0.5
    else:
        duration = _selected_interval(state)
    duration = max(0.05, float(duration))
    state["phase"] = "cooldown"
    state["scheduled_cooldown_sec"] = duration
    state["next_fire_at"] = now + duration
    state["countdown_flash_until"] = 0.0
    if bool(state.get("random_interval", False)):
        _set_status(state, f"Random interval selected: {duration:g}s.", now)
    elif initial:
        _set_status(state, f"Armed. {state['target_slot']} starts in {duration:g}s.", now)
    else:
        _set_status(state, f"Cooldown {duration:g}s after {state['move_label']}.", now)
    return duration


def punish_trainer_overlay_payload(
    state: dict[str, Any] | None,
    now: float | None = None,
) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    now_f = float(_now() if now is None else now)
    enabled = bool(state.get("enabled", False))
    show = bool(state.get("show_countdown", False))
    phase = str(state.get("phase") or "off")
    flash_until = float(state.get("countdown_flash_until") or 0.0)
    remaining = max(0.0, float(state.get("next_fire_at") or 0.0) - now_f)
    visible = enabled and show and (phase == "cooldown" or now_f < flash_until)
    return {
        "enabled": enabled,
        "show": show,
        "visible": visible,
        "phase": phase,
        "remaining": remaining,
        "slot": str(state.get("target_slot") or PUNISH_DEFAULT_SLOT),
        "move_label": _clean_text(state.get("move_label")),
        "random_interval": bool(state.get("random_interval", False)),
        "scheduled_interval": float(state.get("scheduled_cooldown_sec") or 0.0),
        "flash_until": flash_until,
        "now": now_f,
    }

def _request_action(state: dict[str, Any], base: int, now: float, reason: str) -> bool:
    action_id = int(state.get("action_id") or 0) & 0xFFFF
    if action_id <= 0:
        _set_status(state, "Choose a move before arming the trainer.", now)
        return False

    action_addr = base + OFF_ACTION_ID
    request_addr = base + OFF_ACTION_REQUEST
    current_action = _read_u32(action_addr, 0)
    mailbox = _read_u32(request_addr, 0)

    # Attacks and reactions are 0x100 or higher. Waiting below that range keeps
    # the trainer from cancelling another action or forcing through hitstun.
    if current_action >= 0x100:
        _set_status(state, f"Waiting for {state['target_slot']} to regain control.", now)
        return False
    if mailbox != 0:
        _set_status(state, f"Waiting for {state['target_slot']} action mailbox.", now)
        return False

    mailbox_value = _mailbox_value_for_action(action_id)
    if not _write_u32(request_addr, mailbox_value):
        _set_status(state, f"Could not write action mailbox at 0x{request_addr:08X}.", now)
        return False

    attempts = int(state.get("request_attempts") or 0) + 1
    state.update(
        {
            "phase": "requested",
            "request_deadline": now + 0.75,
            "request_attempts": attempts,
            "target_base": base,
            "last_request_addr": request_addr,
            "last_mailbox_value": mailbox_value,
            "countdown_flash_until": now + 0.45 if bool(state.get("enabled", False)) else 0.0,
            "last_trigger": {
                "time": now,
                "slot": state["target_slot"],
                "move": state["move_label"],
                "action_id": action_id,
                "reason": reason,
            },
        }
    )
    _set_status(
        state,
        f"Requested {state['target_slot']} {state['move_label']}.",
        now,
    )
    return True


def tick_punish_trainer(
    state: dict[str, Any] | None,
    snaps: dict | None = None,
    now: float | None = None,
    frame_idx: int | None = None,
) -> dict[str, Any]:
    del frame_idx
    now_f = float(_now() if now is None else now)
    clean = normalize_punish_trainer_state(state)
    if isinstance(state, dict):
        state.clear()
        state.update(clean)
        clean = state

    manual_requested = bool(clean.pop("manual_test_requested", False))
    enabled = bool(clean.get("enabled", False))
    slot = _normalize_slot(clean.get("target_slot"))
    clean["target_slot"] = slot
    base = punish_target_base(slot, snaps)

    previous_base = int(clean.get("target_base") or 0)
    if previous_base and base and previous_base != base:
        _clear_our_request(clean)
        clean["phase"] = "off"
        clean["request_attempts"] = 0
    clean["target_base"] = base

    if not base:
        clean["phase"] = "off"
        _set_status(clean, f"Waiting for {slot} to appear in a match.", now_f)
        return clean

    action_id = int(clean.get("action_id") or 0) & 0xFFFF
    current_action = _read_u32(base + OFF_ACTION_ID, 0)
    clean["last_seen_action"] = current_action
    phase = str(clean.get("phase") or "off")

    if manual_requested:
        clean["manual_only"] = True
        clean["request_attempts"] = 0
        if not _request_action(clean, base, now_f, "manual test"):
            clean["phase"] = "manual_wait"
        phase = str(clean.get("phase") or "manual_wait")

    if not enabled and not bool(clean.get("manual_only", False)):
        _clear_our_request(clean)
        clean["phase"] = "off"
        clean["next_fire_at"] = 0.0
        return clean

    if phase == "off":
        _clear_our_request(clean)
        _schedule_cooldown(clean, now_f, initial=True)
        return clean

    if phase == "manual_wait":
        if _request_action(clean, base, now_f, "manual test"):
            return clean
        return clean

    if phase == "requested":
        if current_action == action_id:
            clean["phase"] = "performing"
            clean["request_attempts"] = 0
            clean["trigger_count"] = int(clean.get("trigger_count") or 0) + 1
            clean["last_request_addr"] = 0
            clean["last_mailbox_value"] = 0
            _set_status(clean, f"{slot} performing {clean['move_label']}.", now_f)
            return clean

        if now_f >= float(clean.get("request_deadline") or 0.0):
            attempts = int(clean.get("request_attempts") or 0)
            if attempts >= 8:
                _clear_our_request(clean)
                if bool(clean.get("manual_only", False)):
                    clean["manual_only"] = False
                    clean["phase"] = "off"
                    _set_status(clean, f"{clean['move_label']} was not accepted.", now_f)
                else:
                    clean["phase"] = "cooldown"
                    clean["next_fire_at"] = now_f + 0.5
                    _set_status(clean, f"Move rejected. Retrying when neutral.", now_f)
                return clean
            if current_action < 0x100 and _read_u32(base + OFF_ACTION_REQUEST, 0) == 0:
                _request_action(clean, base, now_f, "retry")
            else:
                clean["request_deadline"] = now_f + 0.1
        return clean

    if phase == "performing":
        if current_action == action_id:
            return clean
        if current_action < 0x100:
            if bool(clean.get("manual_only", False)):
                clean["manual_only"] = False
                clean["phase"] = "off"
                clean["next_fire_at"] = 0.0
                _set_status(clean, f"Manual {clean['move_label']} complete.", now_f)
            else:
                _schedule_cooldown(clean, now_f)
        return clean

    if phase == "cooldown":
        due = float(clean.get("next_fire_at") or 0.0)
        if now_f >= due:
            if not _request_action(clean, base, now_f, "cooldown"):
                clean["next_fire_at"] = now_f + 0.1
        return clean

    clean["phase"] = "off"
    return clean


def punish_trainer_status(state: dict[str, Any] | None, now: float | None = None) -> str:
    if not isinstance(state, dict):
        return ""
    now_f = float(_now() if now is None else now)
    if bool(state.get("enabled", False)) and str(state.get("phase")) == "cooldown":
        remaining = max(0.0, float(state.get("next_fire_at") or 0.0) - now_f)
        return f"Punish {state.get('target_slot', PUNISH_DEFAULT_SLOT)} {state.get('move_label', '')} in {remaining:.1f}s"
    return _clean_text(state.get("last_status"))


def _move_sort_key(item: tuple[int, str]) -> tuple[int, int, str]:
    action_id, label = item
    if 0x100 <= action_id <= 0x10F:
        group = 0
    elif action_id < 0x180:
        group = 1
    elif action_id < 0x300:
        group = 2
    else:
        group = 3
    return group, action_id, label.casefold()


def punish_roster_context(snaps: dict | None, move_map: dict | None = None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    mapping = move_map if isinstance(move_map, dict) else {}
    for slot in ("P1-C1", "P1-C2", "P2-C1", "P2-C2"):
        snap = (snaps or {}).get(slot) or {}
        base = _snap_base(snap) or punish_target_base(slot, snaps)
        name = _clean_text(snap.get("name") if isinstance(snap, dict) else "")
        try:
            char_id = int((snap or {}).get("csv_char_id"))
        except Exception:
            char_id = -1
        bucket = mapping.get(char_id, {}) if char_id >= 0 else {}
        moves: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()
        if isinstance(bucket, dict):
            pairs = []
            for raw_id, raw_label in bucket.items():
                try:
                    action_id = int(raw_id) & 0xFFFF
                except Exception:
                    continue
                label = _clean_text(raw_label)
                if not label or label.upper().startswith("FLAG_"):
                    continue
                if action_id < 0x100 or action_id >= 0x4000:
                    continue
                pairs.append((action_id, label))
            for action_id, label in sorted(pairs, key=_move_sort_key):
                key = (action_id, label.casefold())
                if key in seen:
                    continue
                seen.add(key)
                moves.append(
                    {
                        "label": label,
                        "action_id": action_id,
                        "display": f"{label} [0x{action_id:03X}]",
                    }
                )
        if not moves:
            for label, action_id in PUNISH_LABEL_ACTIONS.items():
                moves.append(
                    {
                        "label": label,
                        "action_id": action_id,
                        "display": f"{label} [0x{action_id:03X}]",
                    }
                )
        out[slot] = {
            "slot": slot,
            "base": base,
            "name": name or "Waiting",
            "char_id": char_id,
            "moves": moves,
        }
    return out


# Compatibility names retained for older imports.
_load_punish_training_config = load_punish_trainer_config
_save_punish_training_config = save_punish_trainer_config
_tick_punish_training = tick_punish_trainer
_punish_training_roster_context = punish_roster_context
_clear_punish_training_runtime = clear_punish_trainer_runtime
_load_punish_trainer_config = load_punish_trainer_config
_save_punish_trainer_config = save_punish_trainer_config
_tick_punish_trainer = tick_punish_trainer
_punish_roster_context = punish_roster_context
_punish_trainer_overlay_payload = punish_trainer_overlay_payload
