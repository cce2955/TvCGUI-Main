from __future__ import annotations

import threading
import time
from typing import Any

from tvcgui.platform.dolphin import rd32, wd32

P1_FIGHTER_PTR_ADDR = 0x803C9FCC

# Resolver 0x80048270 consumes this field directly:
#   lwz  r3,0x200(r31)
#   if nonzero: clear it, add 0x4000, return
# Therefore the mailbox value must be pre-adjusted by -0x4000. Writing raw
# 0x130 produces action 0x4130. Writing 0xFFFFC130 produces action 0x130.
# The normal action commit path then performs all transition setup.
OFF_ACTION_REQUEST = 0x200
OFF_ACTION_ID = 0x1E8
OFF_PREVIOUS_ACTION_ID = 0x1EC
OFF_NORMALIZED_ACTION_INDEX = 0x204

MOVE_HADOUKEN = 0x00000130

_LOCK = threading.RLock()
_CANCEL = threading.Event()
_WORKER: threading.Thread | None = None
_STATE: dict[str, Any] = {
    "active": False,
    "installed": False,
    "move_id": 0,
    "request_addr": 0,
    "attempts": 0,
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


def get_action_spoof_state() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATE)


def _read_u32(addr: int, default: int = 0) -> int:
    try:
        value = rd32(int(addr))
        if value is None:
            return int(default) & 0xFFFFFFFF
        return int(value) & 0xFFFFFFFF
    except Exception:
        return int(default) & 0xFFFFFFFF


def _write_u32(addr: int, value: int) -> bool:
    try:
        return bool(wd32(int(addr), int(value) & 0xFFFFFFFF))
    except Exception:
        return False


def _fighter_base() -> int:
    fighter = _read_u32(P1_FIGHTER_PTR_ADDR, 0)
    if 0x90000000 <= fighter < 0x94000000:
        return fighter
    return 0


def _mailbox_value_for_action(move_id: int) -> int:
    return (int(move_id) - 0x4000) & 0xFFFFFFFF


def _clear_request_if_ours(request_addr: int, mailbox_value: int) -> bool:
    current = _read_u32(request_addr, 0)
    if current != (int(mailbox_value) & 0xFFFFFFFF):
        return True
    return _write_u32(request_addr, 0)


def restore_action_spoof() -> dict[str, Any]:
    _CANCEL.set()
    with _LOCK:
        request_addr = int(_STATE.get("request_addr", 0) or 0)
        move_id = int(_STATE.get("move_id", 0) or 0)
    mailbox_value = _mailbox_value_for_action(move_id) if move_id else 0
    ok = True
    if request_addr and mailbox_value:
        ok = _clear_request_if_ours(request_addr, mailbox_value)
    _set_state(
        active=False,
        installed=False,
        request_addr=0,
        last_ok=bool(ok),
        last_error="" if ok else "Could not clear the pending action request.",
        last_action="Action Spoof request cleared." if ok else "Action Spoof clear failed.",
        finished_at=_now(),
    )
    return get_action_spoof_state()


def _spoof_worker(move_id: int, timeout_sec: float) -> None:
    move = int(move_id) & 0xFFFFFFFF
    mailbox_value = _mailbox_value_for_action(move)
    started = _now()
    _set_state(
        active=True,
        installed=False,
        move_id=move,
        request_addr=0,
        attempts=0,
        last_ok=False,
        last_error="",
        last_action=f"Requesting action 0x{move:03X}...",
        started_at=started,
        finished_at=0.0,
    )

    success = False
    detail = ""
    request_addr = 0
    attempts = 0
    consumed_once = False

    try:
        fighter = _fighter_base()
        if not fighter:
            raise RuntimeError("P1 fighter pointer is not live; enter a match first")

        request_addr = fighter + OFF_ACTION_REQUEST
        action_addr = fighter + OFF_ACTION_ID
        previous_addr = fighter + OFF_PREVIOUS_ACTION_ID
        _set_state(request_addr=request_addr)

        current_action = _read_u32(action_addr, 0)
        previous_action = _read_u32(previous_addr, 0)
        # previous_action intentionally remains the last completed action after
        # Ryu returns to neutral. Only the live current action can block a new
        # one-shot request.
        if current_action == move:
            raise RuntimeError("P1 is currently using Hadouken; press Action Spoof again after the move ends")

        existing_request = _read_u32(request_addr, 0)
        if existing_request not in (0, mailbox_value):
            raise RuntimeError(
                f"fighter action mailbox is busy with 0x{existing_request:08X}; try again after returning to neutral"
            )

        deadline = _now() + max(0.35, float(timeout_sec))
        next_write = 0.0

        while _now() < deadline and not _CANCEL.is_set():
            current_fighter = _fighter_base()
            if current_fighter != fighter:
                raise RuntimeError("P1 fighter pointer changed while Action Spoof was armed")

            current_action = _read_u32(action_addr, 0)
            previous_action = _read_u32(previous_addr, 0)

            # Confirm only after this worker has sent a fresh request. The
            # previous and normalized fields can retain Hadouken values after
            # control has already returned to the player.
            if attempts > 0 and current_action == move:
                success = True
                detail = (
                    f"Hadouken accepted through 0x{request_addr:08X}; "
                    f"action=0x{current_action:03X} previous=0x{previous_action:03X}."
                )
                break

            now = _now()
            mailbox = _read_u32(request_addr, 0)
            if mailbox == 0 and attempts > 0:
                consumed_once = True

            # Re-arm only while the move has not started. The resolver clears
            # this mailbox itself, so retrying gives the next eligible frame a
            # request without holding or spoofing controller input.
            if now >= next_write and mailbox == 0:
                if not _write_u32(request_addr, mailbox_value):
                    raise RuntimeError(f"failed to write action mailbox at 0x{request_addr:08X}")
                attempts += 1
                _set_state(
                    attempts=attempts,
                    last_action=(
                        f"Hadouken request sent, attempt {attempts}, "
                        f"mailbox=0x{mailbox_value:08X}."
                    ),
                )
                next_write = now + (1.0 / 120.0)

            time.sleep(0.001)

        if _CANCEL.is_set():
            detail = "Action Spoof cancelled."
        elif not success:
            mailbox = _read_u32(request_addr, 0)
            current_action = _read_u32(action_addr, 0)
            previous_action = _read_u32(previous_addr, 0)
            if consumed_once:
                detail = (
                    f"The real action mailbox was consumed {attempts} time(s), but Hadouken was rejected in the "
                    f"current state. action=0x{current_action:03X} previous=0x{previous_action:03X}."
                )
            else:
                detail = (
                    f"The action mailbox was not consumed. mailbox=0x{mailbox:08X} "
                    f"at 0x{request_addr:08X}."
                )
    except Exception as exc:
        detail = str(exc)
    finally:
        clear_ok = True
        if request_addr:
            clear_ok = _clear_request_if_ours(request_addr, mailbox_value)
        if not clear_ok:
            detail = (detail + " Pending request could not be cleared.").strip()
            success = False

        _set_state(
            active=False,
            installed=False,
            request_addr=0,
            attempts=attempts,
            last_ok=bool(success),
            last_error="" if success else detail,
            last_action=("Hadouken spoof confirmed. " + detail).strip()
            if success
            else ("Action Spoof failed: " + detail).strip(),
            finished_at=_now(),
        )
        print(f"[action spoof] {_STATE.get('last_action', '')}", flush=True)


def request_action_spoof(move_id: int = MOVE_HADOUKEN, *, timeout_sec: float = 1.5) -> dict[str, Any]:
    global _WORKER
    with _LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            _STATE["last_action"] = "Action Spoof is already running."
            return dict(_STATE)

        _CANCEL.clear()
        _STATE.update(
            {
                "active": True,
                "move_id": int(move_id) & 0xFFFFFFFF,
                "request_addr": 0,
                "attempts": 0,
                "last_ok": False,
                "last_error": "",
                "last_action": "Hadouken request queued.",
                "started_at": _now(),
                "finished_at": 0.0,
            }
        )
        _WORKER = threading.Thread(
            target=_spoof_worker,
            args=(int(move_id) & 0xFFFFFFFF, float(timeout_sec)),
            name="tvc-action-spoof",
            daemon=True,
        )
        _WORKER.start()

    return get_action_spoof_state()


def request_hadouken_spoof() -> dict[str, Any]:
    return request_action_spoof(MOVE_HADOUKEN, timeout_sec=1.5)
