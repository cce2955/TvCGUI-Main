from __future__ import annotations

from typing import Any


def start_ppc_register_service() -> None:
    return None


def stop_ppc_register_service() -> None:
    return None


def ppc_register_service_status() -> dict[str, Any]:
    return {
        "connected": False,
        "setup": {
            "configured": False,
            "read_only": True,
            "restart_required": False,
        },
        "input_injector": {
            "requested": False,
            "breakpoint_installed": False,
        },
    }


def capture_ppc_registers() -> dict[str, Any]:
    return {
        "available": False,
        "source": "disabled",
        "errors": ["Debugger/register capture is disabled in this build."],
        "restart_required": False,
    }


def arm_input_register_injector(
    target: int,
    value: int,
    *,
    label: str = "input",
) -> dict[str, Any]:
    return input_register_injector_status()


def stop_input_register_injector() -> dict[str, Any]:
    return input_register_injector_status()


def input_register_injector_status() -> dict[str, Any]:
    return {
        "connected": False,
        "requested": False,
        "target": 0,
        "value": 0,
        "label": "read-only",
        "breakpoint_addr": 0,
        "breakpoint_installed": False,
        "hits": 0,
        "matches": 0,
        "rejects": 0,
        "write_failures": 0,
        "last_r28": 0,
        "last_original_r3": 0,
        "last_injected_r3": 0,
        "last_stop_reply": "",
        "last_status": "Debugger/register injection is disabled.",
        "service_error": "",
        "port": 0,
        "restart_required": False,
        "setup": {
            "configured": False,
            "read_only": True,
            "restart_required": False,
        },
    }


__all__ = [
    "start_ppc_register_service",
    "stop_ppc_register_service",
    "ppc_register_service_status",
    "capture_ppc_registers",
    "arm_input_register_injector",
    "stop_input_register_injector",
    "input_register_injector_status",
]
