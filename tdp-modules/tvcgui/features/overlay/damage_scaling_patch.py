"""Compatibility stub for the retired damage-scaling import hook.

The live integration now imports damage_scaling directly from the HUD manager
and renderer. Keeping this no-op function prevents an older package initializer
from wrapping HudOverlayManager.write_data again.
"""
from __future__ import annotations


def install_damage_scaling_patch() -> None:
    return None
