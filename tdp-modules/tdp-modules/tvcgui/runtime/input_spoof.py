from __future__ import annotations

from typing import Any

_STATE = {
    "installed": False,
    "runtime_ready": False,
    "active": False,
    "mode": 0,
    "label": "read-only",
    "last_error": "",
    "last_status": "Input Monitor is read-only.",
}


def install_input_hook() -> bool:
    return False


def uninstall_input_hook() -> bool:
    return True


def hold_5a(target_base: int) -> bool:
    return False


def hold_2a(target_base: int) -> bool:
    return False


def release_5a(target_base: int = 0) -> bool:
    return True


def pulse_5a(
    target_base: int,
    *,
    hold_frames: int = 4,
    gap_frames: int = 3,
    repeat: bool = False,
) -> bool:
    return False


def pulse_2a(
    target_base: int,
    *,
    hold_frames: int = 4,
    gap_frames: int = 3,
    repeat: bool = False,
) -> bool:
    return False


def stop_input_spoof(*, force_neutral: bool = True) -> bool:
    return True


def clear_input_spoof(*, keep_hook: bool = True) -> bool:
    return True


def emergency_neutral_scrub(target_base: int = 0) -> bool:
    return True


def get_input_spoof_state() -> dict[str, Any]:
    return {
        "installed": False,
        "runtime_ready": False,
        "restart_required": False,
        "config_path": "",
        "enabled": 0,
        "mode": 0,
        "target_base": 0,
        "current_mask": 0,
        "hit_count": 0,
        "match_count": 0,
        "reject_count": 0,
        "write_failures": 0,
        "last_original_input": 0,
        "last_input": 0,
        "last_r28": 0,
        "last_downstream_held": 0,
        "last_downstream_pressed": 0,
        "last_action_id": 0,
        "source_slot": -1,
        "source_addr": 0,
        "source_type": 0,
        "cave_version": 0,
        "cave_addr": 0,
        "mailbox_addr": 0,
        "hook_site": 0,
        "hook_expected": 0,
        "hook_original": 0,
        "breakpoint_installed": False,
        "gdb_port": 0,
        "last_stop_reply": "",
        "last_error": "",
        "last_action": "Input Monitor is read-only.",
        "label": "read-only",
        "removed_gecko_paths": [],
    }


def trigger_5a(target_base: int) -> bool:
    return False


def queue_preset(
    target_base: int,
    name: str,
    *,
    hold_frames: int = 60,
    button_frames: int = 3,
    motion_step_frames: int = 3,
) -> bool:
    return False


def cancel_input_spoof() -> bool:
    return True


__all__ = [
    "install_input_hook",
    "uninstall_input_hook",
    "hold_5a",
    "hold_2a",
    "release_5a",
    "pulse_5a",
    "pulse_2a",
    "stop_input_spoof",
    "clear_input_spoof",
    "emergency_neutral_scrub",
    "get_input_spoof_state",
    "trigger_5a",
    "queue_preset",
    "cancel_input_spoof",
]
