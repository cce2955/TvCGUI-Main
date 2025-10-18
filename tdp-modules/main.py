# main.py
# *** Minimal HUD + Pooled-life watcher (+0x02E) ***
# Shows P1/P2 C1/C2 status, meters, and per-slot pooled life with delta.

from __future__ import annotations
import time, sys
from typing import Dict, Optional, Tuple, List

# --- Local modules (only what we need) ---
from colors import Colors
from config import SHOW_HUD, HUD_REFRESH_HZ, INTERVAL
from constants import SLOTS
from dolphin_io import hook, rbytes
from hud import fmt_line, render_screen
from meter import read_meter, drop_meter_cache_for_base
from models import Fighter, read_fighter
from resolver import SlotResolver, pick_posy_off_no_jump

# ---------------- Helpers ----------------

def _fighter_to_blk(f: Optional[Fighter]) -> Optional[dict]:
    if f is None:
        return None
    return {
        "base": f.base, "max": f.max, "cur": f.cur, "aux": f.aux,
        "id": f.id, "name": f.name, "x": f.x, "y": f.y, "last": f.last,
    }

def _read_pool_byte(base: Optional[int]) -> Optional[int]:
    """Read pooled-life byte at +0x02E; return int 0..255 or None."""
    if not base:
        return None
    raw = rbytes(base + 0x02E, 1)
    if not raw or len(raw) != 1:
        return None
    return int(raw[0])  # 0..255

# ---------------- Main ----------------

def main():
    print(Colors.BOLD + "Minimal HUD + pooled-life: waiting for Dolphin…" + Colors.RESET)
    hook()
    print("Hooked.")

    resolver = SlotResolver()
    last_base_by_slot: Dict[int, int] = {}
    y_off_by_base: Dict[int, int] = {}

    meter_prev = {"P1": 0, "P2": 0}
    meter_now  = {"P1": 0, "P2": 0}
    p1_c1_base: Optional[int] = None
    p2_c1_base: Optional[int] = None

    # pooled-life trackers keyed by slot label ("P1-C1", ...)
    pool_prev: Dict[str, Optional[int]] = {}
    pool_now:  Dict[str, Optional[int]] = {}

    if SHOW_HUD:
        sys.stdout.write("\033[?25l")  # hide cursor
        sys.stdout.flush()
    _last_hud = 0.0
    HUD_REFRESH_INTERVAL = 1.0 / max(1, HUD_REFRESH_HZ)

    try:
        while True:
            # Resolve bases for each slot
            resolved: List[Tuple[str, int, Optional[int], bool]] = []
            for name, slot in SLOTS:
                base, changed = resolver.resolve_base(slot)
                resolved.append((name, slot, base, changed))

            # Handle base changes & Y offset selection
            for name, slot, base, changed in resolved:
                if base and last_base_by_slot.get(slot) != base:
                    last_base_by_slot[slot] = base
                    drop_meter_cache_for_base(base)
                    y_off_by_base[base] = pick_posy_off_no_jump(base)
                    # reset pool_prev for this slot on base change to avoid fake deltas
                    pool_prev[name] = None
                    pool_now[name] = None

            # Identify team leaders' bases for meter reads
            for name, slot, base, _ in resolved:
                if name == "P1-C1" and base: p1_c1_base = base
                if name == "P2-C1" and base: p2_c1_base = base

            # Meters
            meter_prev["P1"], meter_prev["P2"] = meter_now["P1"], meter_now["P2"]
            m1, m2 = read_meter(p1_c1_base), read_meter(p2_c1_base)
            if m1 is not None: meter_now["P1"] = m1
            if m2 is not None: meter_now["P2"] = m2

            # Fighter snapshots + pooled-life reads
            info: Dict[str, Optional[Fighter]] = {}
            for name, slot, base, _ in resolved:
                if base:
                    y = y_off_by_base.get(base, 0xF4)
                    info[name] = read_fighter(base, y)
                    # pooled-life tracker
                    pool_prev[name] = pool_now.get(name, None)
                    pool_now[name] = _read_pool_byte(base)
                else:
                    info[name] = None
                    pool_prev[name] = None
                    pool_now[name] = None

            t = time.time()

            # HUD render (static list + meter summary + pooled-life lines)
            if SHOW_HUD and (t - _last_hud) >= HUD_REFRESH_INTERVAL:
                _last_hud = t
                lines: List[str] = []
                mP1 = meter_now["P1"] if info.get("P1-C1") else None
                mP2 = meter_now["P2"] if info.get("P2-C1") else None

                for nm, _ in SLOTS:
                    blk = _fighter_to_blk(info.get(nm))
                    meter = mP1 if nm == "P1-C1" else (mP2 if nm == "P2-C1" else None)
                    lines.append(fmt_line(nm, blk, meter))

                meter_summary = (
                    f"{Colors.PURPLE}Meters{Colors.RESET}  "
                    f"P1:{meter_now['P1']:>6} (Δ{(meter_now['P1']-meter_prev['P1']):+})  "
                    f"P2:{meter_now['P2']:>6} (Δ{(meter_now['P2']-meter_prev['P2']):+})"
                )

                # Extra lines: pooled-life per slot (value + delta)
                extras: List[str] = []
                for nm, _ in SLOTS:
                    cur = pool_now.get(nm, None)
                    prv = pool_prev.get(nm, None)
                    if cur is None:
                        extras.append(f"{nm} pool: --")
                    else:
                        delta = (cur - prv) if (prv is not None) else 0
                        # lightly color deltas: up=green, down=red, flat=dim
                        if prv is None:
                            dtxt = f"Δ{0:+}"
                        elif delta > 0:
                            dtxt = f"{Colors.GREEN}Δ{delta:+}{Colors.RESET}"
                        elif delta < 0:
                            dtxt = f"{Colors.RED}Δ{delta:+}{Colors.RESET}"
                        else:
                            dtxt = f"Δ{delta:+}" 
                        extras.append(f"{nm} pool(+0x02E): {cur:>3}  {dtxt}")

                render_screen(lines, meter_summary, extras)

            time.sleep(INTERVAL)

    except KeyboardInterrupt:
        pass
    finally:
        if SHOW_HUD:
            sys.stdout.write("\033[?25h")  # show cursor
            sys.stdout.flush()

if __name__ == "__main__":
    main()
