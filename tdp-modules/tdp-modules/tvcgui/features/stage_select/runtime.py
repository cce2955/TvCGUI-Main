"""Direct live stage-code controls.

This is intentionally the small, proven part of the stage work: read and
write the raw carousel code.  There is no FPK work, no stage-row patching, no
sequence scan, and no hard validation gate that can lock the controls out.

The static cursor mirror is always safe to read/write.  When the in-game stage
carousel is currently live, a small self-consistency check also finds the
second runtime mirror and writes it in the same one-shot action.
"""
from __future__ import annotations

import threading
import time
from typing import Any

try:
    from tvcgui.platform.dolphin import rd32, wd32
except Exception:  # pragma: no cover
    rd32 = None
    wd32 = None

_LOCK = threading.RLock()
_WORKER_LOCK = threading.Lock()

# This static mirror is the raw cursor value used by the working iterator.
# It is the one stable address the GUI can always grab without trying to
# identify a loaded menu object.
STAGE_GLOBAL_SELECTION = 0x809BCEE0

# The game exposes the matching UI/runtime cursor through a live object while
# the stage carousel is present.  The root slot can legitimately be stale
# outside that scene, so it is an optional second write, never a precondition.
STAGE_RUNTIME_PTR = 0x809BCE28
RUNTIME_ENTRY_COUNT_OFF = 0x04
RUNTIME_SELF_PTR_OFF = 0x4C
RUNTIME_SELECTION_OFF = 0x6C
MEM2_LO = 0x90000000
MEM2_HI = 0x94000000

TEST_CODE_MIN = 0
TEST_CODE_MAX = 0xFFFFFFFF
RAW_CODE_DEFAULT = 0

# Slot-2 replacement route.  The Stage Control UI remains a raw-ID tool: it
# still writes exactly the requested cursor ID.  The only added behavior is
# that applying ID 2 while the live carousel exists also prepares the resolved
# load result for Yami's arena.  The selected tile remains the normal ID-2
# tile; the substitution happens only at confirmation/load time.
YAMI_REPLACEMENT_SLOT = 2
STAGE_RESULT_ID = 0x803EB8F4
YAMI_STAGE_ASSET_ID = 0x0000000F
YAMI_SLOT_ARM_TIMEOUT_SEC = 45.0
YAMI_SLOT_HOLD_SEC = 2.25
YAMI_SLOT_POLL_SEC = 0.006

_STATE: dict[str, Any] = {
    "last_action": "Ready. Grab or apply a raw stage ID.",
    "last_error": "",
    "last_write": None,
    "last_read": None,
    "worker_busy": False,
    "runtime_hint": None,
    "runtime_hint_at": 0.0,
    "slot2_yami": {
        "active": False,
        "phase": "idle",
        "baseline": None,
        "runtime": None,
        "writes": 0,
        "detail": "",
    },
}

_SLOT2_YAMI_LOCK = threading.RLock()
_SLOT2_YAMI_CANCEL: threading.Event | None = None


def _u32(addr: int | None) -> int | None:
    if rd32 is None or addr is None:
        return None
    try:
        value = rd32(int(addr))
        return int(value) if value is not None else None
    except Exception:
        return None


def _fmt(value: int | None) -> str:
    return "--" if value is None else f"0x{int(value) & 0xFFFFFFFF:08X}"


def _in_mem2(value: int | None) -> bool:
    return value is not None and MEM2_LO <= int(value) < MEM2_HI


def _live_runtime() -> int | None:
    """Return the runtime only when the root currently names a stage object.

    This is deliberately light: it does not care about the old sequence
    header/table assumptions that caused the previous window to refuse every
    action after a reset.  It only prevents a second write into a recycled
    heap allocation.
    """
    root = _u32(STAGE_RUNTIME_PTR)
    if _in_mem2(root):
        runtime = int(root)
        if _u32(runtime + RUNTIME_ENTRY_COUNT_OFF) == 15 and _u32(runtime + RUNTIME_SELF_PTR_OFF) == runtime:
            with _LOCK:
                _STATE["runtime_hint"] = runtime
                _STATE["runtime_hint_at"] = time.time()
            return runtime

    # A cached runtime is usable only while it is still self-consistent.
    with _LOCK:
        cached = _STATE.get("runtime_hint")
    if _in_mem2(cached):
        runtime = int(cached)
        if _u32(runtime + RUNTIME_ENTRY_COUNT_OFF) == 15 and _u32(runtime + RUNTIME_SELF_PTR_OFF) == runtime:
            return runtime
    return None


def _set_slot2_yami_state(**updates: Any) -> None:
    with _LOCK:
        record = dict(_STATE.get("slot2_yami") or {})
        record.update(updates)
        _STATE["slot2_yami"] = record


def _slot2_yami_snapshot() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATE.get("slot2_yami") or {})


def _finish_slot2_yami(*, detail: str, error: str = "") -> None:
    _set_slot2_yami_state(active=False, phase="idle", detail=str(detail))
    with _LOCK:
        if error:
            _STATE["last_error"] = str(error)
            _STATE["last_action"] = "ID 2 Yami route stopped."
        else:
            _STATE["last_error"] = ""
            _STATE["last_action"] = str(detail)


def _restore_slot2_baseline(baseline: int | None) -> bool:
    """Undo the pre-arm while the player is still browsing or backs out."""
    if baseline is None or wd32 is None:
        return False
    try:
        return bool(wd32(STAGE_RESULT_ID, int(baseline))) and _u32(STAGE_RESULT_ID) == int(baseline)
    except Exception:
        return False


def _run_slot2_yami_override(
    *,
    starting_runtime: int,
    baseline_value: int | None,
    cancel_event: threading.Event,
) -> None:
    """Convert the normal ID-2 selection into Yami's resolved arena at load.

    The raw cursor is deliberately left alone.  The worker waits quietly while
    ID 2 is highlighted, then catches the game's normal result write at
    confirmation and holds the observed Yami result ID only across the short
    loader handoff.  Moving off ID 2 before confirming restores the pre-arm
    value and leaves no route active.
    """
    global _SLOT2_YAMI_CANCEL
    started = time.monotonic()
    deadline = started + YAMI_SLOT_ARM_TIMEOUT_SEC
    hold_deadline: float | None = None
    writes = 0
    phase = "waiting_for_confirm"
    _set_slot2_yami_state(
        active=True,
        phase=phase,
        baseline=baseline_value,
        runtime=starting_runtime,
        writes=writes,
        detail="ID 2 selected. Yami stage route is armed for the next normal confirm.",
    )
    with _LOCK:
        _STATE["last_error"] = ""
        _STATE["last_action"] = "Applied raw stage ID 2. Yami stage route is armed for the next confirm."

    try:
        while not cancel_event.is_set():
            now = time.monotonic()
            raw_code = _u32(STAGE_GLOBAL_SELECTION)
            runtime = _live_runtime()
            current_result = _u32(STAGE_RESULT_ID)
            runtime_left = runtime is None or int(runtime) != int(starting_runtime)

            if hold_deadline is None:
                # User kept browsing instead of confirming ID 2.  Restore the
                # field immediately so no later selection inherits this route.
                if raw_code != YAMI_REPLACEMENT_SLOT and not runtime_left:
                    restored = _restore_slot2_baseline(baseline_value)
                    _finish_slot2_yami(
                        detail=(
                            "ID 2 Yami route cancelled because the cursor moved away from ID 2."
                            + (" Pre-arm value restored." if restored else "")
                        )
                    )
                    return

                # A normal stage confirmation overwrites the pre-arm value.
                # Once that happens, or the stage menu tears down with ID 2
                # still selected, keep Yami's resource ID alive only through
                # the brief stage-loader transition.
                if current_result is not None and current_result != YAMI_STAGE_ASSET_ID:
                    hold_deadline = now + YAMI_SLOT_HOLD_SEC
                    phase = "holding_after_confirm"
                    _set_slot2_yami_state(
                        phase=phase,
                        detail="ID 2 confirmed. Replacing the resolved load stage with Yami's arena.",
                    )
                    with _LOCK:
                        _STATE["last_action"] = "ID 2 confirmed. Holding Yami's arena through the load handoff."
                elif runtime_left:
                    hold_deadline = now + YAMI_SLOT_HOLD_SEC
                    phase = "holding_after_confirm"
                    _set_slot2_yami_state(
                        phase=phase,
                        detail="Stage menu left with ID 2 armed. Holding Yami's arena through the load handoff.",
                    )
                    with _LOCK:
                        _STATE["last_action"] = "Stage menu left with ID 2 armed. Holding Yami's arena through the load handoff."
                elif now >= deadline:
                    restored = _restore_slot2_baseline(baseline_value)
                    _finish_slot2_yami(
                        detail=(
                            "ID 2 Yami route timed out without a confirm."
                            + (" Pre-arm value restored." if restored else "")
                        )
                    )
                    return

            if hold_deadline is not None:
                if wd32 is None:
                    _finish_slot2_yami(
                        detail="ID 2 Yami route stopped.",
                        error="Dolphin write support is unavailable.",
                    )
                    return
                try:
                    ok = bool(wd32(STAGE_RESULT_ID, YAMI_STAGE_ASSET_ID))
                    after = _u32(STAGE_RESULT_ID)
                except Exception as exc:
                    _finish_slot2_yami(
                        detail="ID 2 Yami route stopped.",
                        error=f"Yami result write failed: {exc!r}",
                    )
                    return
                writes += 1
                _set_slot2_yami_state(writes=writes)
                if not ok or after != YAMI_STAGE_ASSET_ID:
                    _finish_slot2_yami(
                        detail="ID 2 Yami route stopped.",
                        error="Yami resolved-stage write did not verify.",
                    )
                    return
                if now >= hold_deadline:
                    _finish_slot2_yami(
                        detail=f"ID 2 Yami route finished after {writes} verified loader writes.",
                    )
                    return

            time.sleep(YAMI_SLOT_POLL_SEC)
    except Exception as exc:  # pragma: no cover
        _finish_slot2_yami(
            detail="ID 2 Yami route stopped.",
            error=f"ID 2 Yami route failed: {exc!r}",
        )
    finally:
        with _SLOT2_YAMI_LOCK:
            if _SLOT2_YAMI_CANCEL is cancel_event:
                _SLOT2_YAMI_CANCEL = None


def _arm_slot2_yami_override(runtime: int | None) -> tuple[bool, str]:
    """Pre-arm the Yami load substitution for a verified ID-2 cursor write."""
    global _SLOT2_YAMI_CANCEL
    if runtime is None:
        return False, "ID 2 was written, but the live carousel is not present so no Yami route was armed."
    if wd32 is None:
        return False, "ID 2 was written, but Dolphin write support is unavailable for the Yami route."

    with _SLOT2_YAMI_LOCK:
        existing = _SLOT2_YAMI_CANCEL
        existing_state = _slot2_yami_snapshot()
        if existing is not None and not existing.is_set():
            if existing_state.get("runtime") == int(runtime):
                return True, "ID 2 remains armed for Yami's stage."
            existing.set()

        baseline = _u32(STAGE_RESULT_ID)
        try:
            prearm_ok = bool(wd32(STAGE_RESULT_ID, YAMI_STAGE_ASSET_ID))
            prearm_value = _u32(STAGE_RESULT_ID)
        except Exception as exc:
            return False, f"ID 2 was written, but Yami pre-arm failed: {exc!r}"
        if not prearm_ok or prearm_value != YAMI_STAGE_ASSET_ID:
            return False, "ID 2 was written, but the Yami stage-result pre-arm did not verify."

        cancel_event = threading.Event()
        _SLOT2_YAMI_CANCEL = cancel_event
        _set_slot2_yami_state(
            active=True,
            phase="starting",
            baseline=baseline,
            runtime=int(runtime),
            writes=1,
            detail="ID 2 selected. Starting Yami stage route.",
        )
        threading.Thread(
            target=_run_slot2_yami_override,
            kwargs={
                "starting_runtime": int(runtime),
                "baseline_value": baseline,
                "cancel_event": cancel_event,
            },
            name="StageId2YamiRoute",
            daemon=True,
        ).start()
    return True, "ID 2 selected. Yami stage route is armed for the next confirm."


def _cancel_slot2_yami_if_active(*, restore: bool) -> None:
    """Disarm a pending ID-2 route when a different raw cursor is applied."""
    global _SLOT2_YAMI_CANCEL
    with _SLOT2_YAMI_LOCK:
        event = _SLOT2_YAMI_CANCEL
        _SLOT2_YAMI_CANCEL = None
    if event is None:
        return
    record = _slot2_yami_snapshot()
    event.set()
    restored = _restore_slot2_baseline(record.get("baseline")) if restore else False
    _set_slot2_yami_state(
        active=False,
        phase="idle",
        detail="ID 2 Yami route cancelled by a different raw stage ID."
               + (" Pre-arm value restored." if restored else ""),
    )


def _snapshot() -> dict[str, Any]:
    global_code = _u32(STAGE_GLOBAL_SELECTION)
    root = _u32(STAGE_RUNTIME_PTR)
    runtime = _live_runtime()
    runtime_code = _u32(runtime + RUNTIME_SELECTION_OFF) if runtime is not None else None
    return {
        "global_code": global_code,
        "runtime_code": runtime_code,
        "runtime": runtime,
        "root": root,
        "runtime_live": runtime is not None,
    }


def _run_worker(label: str, fn: Any) -> dict[str, Any]:
    with _WORKER_LOCK:
        with _LOCK:
            if _STATE.get("worker_busy"):
                _STATE["last_action"] = "Stage ID operation is already running."
                _STATE["last_error"] = ""
                return get_stage_probe_state()
            _STATE["worker_busy"] = True
            _STATE["last_action"] = f"{label}…"
            _STATE["last_error"] = ""

    def _worker() -> None:
        try:
            fn()
        except Exception as exc:  # pragma: no cover
            with _LOCK:
                _STATE["last_action"] = "Stage ID operation stopped."
                _STATE["last_error"] = f"{label} failed: {exc!r}"
        finally:
            with _LOCK:
                _STATE["worker_busy"] = False

    threading.Thread(target=_worker, name="StageIdWorker", daemon=True).start()
    return get_stage_probe_state()


def read_stage_selection() -> dict[str, Any]:
    snapshot = _snapshot()
    with _LOCK:
        _STATE["last_read"] = dict(snapshot)
        _STATE["last_error"] = ""
        if snapshot["runtime_live"]:
            _STATE["last_action"] = (
                f"Grabbed raw stage ID {snapshot['global_code']} from the global mirror and "
                f"{snapshot['runtime_code']} from the live carousel mirror."
            )
        else:
            _STATE["last_action"] = (
                f"Grabbed raw stage ID {snapshot['global_code']} from the global mirror. "
                "The stage carousel runtime is not live yet."
            )
    return get_stage_probe_state()


def request_stage_probe() -> dict[str, Any]:
    return _run_worker("Grabbing the live stage ID", read_stage_selection)


def set_stage_selection(code: int, *, label: str | None = None) -> dict[str, Any]:
    try:
        target = int(code)
    except Exception:
        target = -1
    if not TEST_CODE_MIN <= target <= TEST_CODE_MAX:
        with _LOCK:
            _STATE["last_error"] = "Stage ID must be an unsigned 32-bit value."
            _STATE["last_action"] = "No stage ID write was sent."
        return get_stage_probe_state()
    if wd32 is None:
        with _LOCK:
            _STATE["last_error"] = "Dolphin write support is unavailable."
            _STATE["last_action"] = "No stage ID write was sent."
        return get_stage_probe_state()

    # Keep the direct control exactly one-shot for every other ID.  Only an
    # actual ID-2 request is allowed to carry a Yami load-route worker.
    if target != YAMI_REPLACEMENT_SLOT:
        _cancel_slot2_yami_if_active(restore=True)

    before = _snapshot()
    global_ok = False
    runtime_ok: bool | None = None
    runtime_addr: int | None = None
    write_error = ""
    try:
        # Always write the stable raw-ID mirror.  This is the path that must
        # remain usable even when a reset has invalidated the old heap object.
        global_ok = bool(wd32(STAGE_GLOBAL_SELECTION, target))

        # When the actual carousel is live, mirror the same raw code into the
        # current runtime object.  It is optional, so a stale root cannot veto
        # the global write or turn the UI into a refusal screen.
        runtime = _live_runtime()
        if runtime is not None:
            runtime_addr = runtime + RUNTIME_SELECTION_OFF
            runtime_ok = bool(wd32(runtime_addr, target))
    except Exception as exc:  # pragma: no cover
        write_error = repr(exc)

    # Let the menu's next update consume the static mirror before sampling it.
    time.sleep(0.015)
    after = _snapshot()
    global_verified = after["global_code"] == target
    runtime_verified = (
        runtime_ok is None
        or (after["runtime_live"] and after["runtime_code"] == target)
    )
    verified = bool(global_ok and global_verified and runtime_verified)
    record = {
        "target": target,
        "label": str(label or f"raw stage ID {target}"),
        "before_global": before["global_code"],
        "before_runtime": before["runtime_code"],
        "after_global": after["global_code"],
        "after_runtime": after["runtime_code"],
        "global_write_ok": global_ok,
        "runtime_write_ok": runtime_ok,
        "runtime_address": runtime_addr,
        "verified": verified,
    }
    with _LOCK:
        _STATE["last_write"] = record
        _STATE["last_read"] = dict(after)
        if verified:
            _STATE["last_error"] = ""
            if runtime_ok is None:
                _STATE["last_action"] = (
                    f"Applied raw stage ID {target} to the stable cursor mirror. "
                    "Open the stage carousel and press Apply again to mirror it into the live UI."
                )
            else:
                _STATE["last_action"] = f"Applied raw stage ID {target}."
        else:
            detail = write_error or "the write did not remain in the raw cursor mirror"
            _STATE["last_error"] = f"Raw stage ID write did not verify: {detail}."
            _STATE["last_action"] = "No ongoing write loop was left running."

    # Stage Control itself is intentionally unchanged.  This is the sole
    # conditional add-on: applying a verified ID 2 on a live carousel arms the
    # known Yami result ID for that selection's normal confirm.
    if verified and target == YAMI_REPLACEMENT_SLOT:
        armed, detail = _arm_slot2_yami_override(after.get("runtime"))
        with _LOCK:
            if armed:
                _STATE["last_error"] = ""
                _STATE["last_action"] = detail
            else:
                _STATE["last_error"] = detail
                _STATE["last_action"] = "Applied raw stage ID 2, but Yami routing was not armed."
    return get_stage_probe_state()


def request_stage_selection(code: int, *, label: str | None = None) -> dict[str, Any]:
    return _run_worker(
        f"Applying raw stage ID {int(code) if isinstance(code, int) else code}",
        lambda: set_stage_selection(code, label=label),
    )


def get_stage_probe_state(*, refresh: bool = False) -> dict[str, Any]:
    if refresh:
        read_stage_selection()
    with _LOCK:
        read = dict(_STATE.get("last_read") or {})
        return {
            "mode": "direct_raw_stage_id",
            "last_action": str(_STATE.get("last_action") or ""),
            "last_error": str(_STATE.get("last_error") or ""),
            "last_write": dict(_STATE.get("last_write") or {}),
            "last_read": read,
            "worker_busy": bool(_STATE.get("worker_busy")),
            "addresses": {
                "global_selection": _fmt(STAGE_GLOBAL_SELECTION),
                "runtime_root": _fmt(STAGE_RUNTIME_PTR),
                "runtime_selection": _fmt((int(read["runtime"]) + RUNTIME_SELECTION_OFF) if read.get("runtime") is not None else None),
            },
            "test_code_min": TEST_CODE_MIN,
            "test_code_max": TEST_CODE_MAX,
            "slot2_yami": dict(_STATE.get("slot2_yami") or {}),
        }


# Compatibility shims for old imports.  The Stage Select window intentionally
# exposes only raw ID grab/apply controls until that route is solid again.
def request_arm_yami_stage() -> dict[str, Any]:
    with _LOCK:
        _STATE["last_error"] = "Yami arming is intentionally disabled in the raw-ID build."
        _STATE["last_action"] = "Use the raw stage ID controls first."
    return get_stage_probe_state()


def cancel_stage_result_override() -> dict[str, Any]:
    return get_stage_probe_state()


def select_stage_code_then_arm_yami(code: int, *, label: str | None = None) -> dict[str, Any]:
    return set_stage_selection(code, label=label)


def select_yami_stage_slot() -> dict[str, Any]:
    return set_stage_selection(16, label="raw stage ID 16")


def move_to_stage_code(code: int) -> dict[str, Any]:
    return set_stage_selection(code, label=f"raw stage ID {int(code)}")


def tick_stage_probe() -> None:
    return None


def tick_stage_test() -> None:
    return None


def get_stage_patch_state() -> dict[str, Any]:
    return get_stage_probe_state()


def queue_stage_capture(label: str) -> dict[str, Any]:
    with _LOCK:
        _STATE["last_error"] = "Capture mode is not part of the direct raw-ID control."
        _STATE["last_action"] = "No file was created."
    return get_stage_probe_state()


def queue_stage_clear() -> dict[str, Any]:
    return get_stage_probe_state()
