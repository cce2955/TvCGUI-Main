# main.py
# Minimal HUD + pooled-life + gate watcher + Startup-only (checkpoint)
# - t0 from edges ONLY: 058↑ / 059↑ / 060≠baseline
# - STARTUP = duration of 0x072 being non-zero (per your observations)
# - WINDOW = (060!=baseline) or (072!=0)
# - WINDOW END when (060==baseline) AND (072==0) for HYST_END frames (debounce)

from __future__ import annotations
import time, sys
from typing import Dict, Optional, Tuple, List

from colors import Colors
from config import SHOW_HUD, HUD_REFRESH_HZ, INTERVAL
from constants import SLOTS
from dolphin_io import hook, rbytes
from hud import fmt_line, render_screen
from meter import read_meter, drop_meter_cache_for_base
from models import Fighter, read_fighter
from resolver import SlotResolver, pick_posy_off_no_jump

# ================= Tunables =================
FPS = 60.0
POOL_REL = 0x02E

# Gate bytes we watch (original set)
REL_058 = 0x058
REL_059 = 0x059
REL_060 = 0x060
REL_064 = 0x064
REL_066 = 0x066
REL_072 = 0x072
WATCH = (REL_058, REL_059, REL_060, REL_064, REL_066, REL_072)

# ================= Helpers =================
def _fighter_to_blk(f: Optional[Fighter]) -> Optional[dict]:
    if f is None: return None
    return {
        "base": f.base, "max": f.max, "cur": f.cur, "aux": f.aux,
        "id": f.id, "name": f.name, "x": f.x, "y": f.y, "last": f.last,
    }

def _rd8(base: Optional[int], rel: int) -> Optional[int]:
    if not base: return None
    raw = rbytes(base + rel, 1)
    if not raw or len(raw) != 1: return None
    return int(raw[0])

def _read_pool_byte(base: Optional[int]) -> Optional[int]:
    return _rd8(base, POOL_REL)

# ================= Startup-only tracker (checkpoint) =================
class MoveTracker:
    """
    Checkpoint version: TRACKS STARTUP ONLY.
    - t0 anchor: 058↑ / 059↑ / 060≠baseline (edges only)
    - Startup is measured from the length of the 0x072 pulse (frames where 0x072 != 0)
    - Window = (060!=baseline) or (072!=0)
    - Window ends when (060==baseline) and (072==0) for HYST_END_FRAMES
    """

    HYST_END_FRAMES = 2
    FPS = FPS

    def __init__(self, side_hint: str = ""):
        self.side_hint = side_hint
        self.baseline_060: Optional[int] = None

        self.prev_vals: Dict[int, Optional[int]] = {r: None for r in WATCH}
        self.now_vals:  Dict[int, Optional[int]] = {r: None for r in WATCH}

        # window state
        self.in_window: bool = False
        self.t_window_start: Optional[float] = None
        self.t_window_end: Optional[float] = None

        # t0 anchor (edges only)
        self.t0_edge: Optional[float] = None
        self.t0_src: Optional[str] = None

        # 0x072 pulse timing
        self._t_072_on: Optional[float] = None
        self._t_072_off: Optional[float] = None

        # debounce counter for window end
        self._zero_frames_after_base: int = 0

        # last computed result (startup + total only)
        self.last_result: Optional[Dict[str, int]] = None
        self.last_072_raw_frames: Optional[int] = None

    # ----- helpers -----
    def _pref_baseline(self) -> Optional[int]:
        if self.baseline_060 is not None:
            return self.baseline_060
        if self.side_hint.startswith("P1"): return 0x04
        if self.side_hint.startswith("P2"): return 0x44
        return None

    def _fr(self, dt: float) -> int:
        return max(0, int((dt * self.FPS) + 0.5))

    def _rise(self, rel: int) -> bool:
        a, b = self.prev_vals.get(rel), self.now_vals.get(rel)
        return (a is not None and b is not None and a == 0 and b != 0)

    def _window_now(self) -> bool:
        v60 = self.now_vals.get(REL_060)
        b60 = self._pref_baseline()
        if v60 is not None and b60 is not None and v60 != b60:
            return True
        v72 = self.now_vals.get(REL_072)
        return (v72 is not None and v72 != 0)

    # ----- IO snapshot -----
    def update_vals(self, base: Optional[int]):
        # rotate
        for r in WATCH:
            self.prev_vals[r] = self.now_vals[r]
        if base:
            for r in WATCH:
                self.now_vals[r] = _rd8(base, r)
        else:
            for r in WATCH:
                self.now_vals[r] = None

        # learn/confirm baseline for 060 when at preferred idle
        v060 = self.now_vals.get(REL_060)
        if v060 is not None:
            pref = 0x04 if self.side_hint.startswith("P1") else (0x44 if self.side_hint.startswith("P2") else None)
            if self.baseline_060 is None:
                if pref is not None and v060 == pref:
                    self.baseline_060 = v060
            else:
                if pref is not None and v060 == pref:
                    self.baseline_060 = v060

    # ----- tick -----
    def tick(self, t: float):
        window_now = self._window_now()

        # window start
        if (not self.in_window) and window_now:
            self.in_window = True
            self.t_window_start = t
            self.t_window_end = None
            self.t0_edge = None
            self.t0_src = None
            self._t_072_on = None
            self._t_072_off = None
            self._zero_frames_after_base = 0

        # t0 from edges
        if self.in_window and self.t0_edge is None:
            if self._rise(REL_058):
                self.t0_edge, self.t0_src = t, "058↑"
            elif self._rise(REL_059):
                self.t0_edge, self.t0_src = t, "059↑"
            else:
                a60 = self.prev_vals.get(REL_060)
                b60 = self._pref_baseline()
                v60 = self.now_vals.get(REL_060)
                if a60 is not None and b60 is not None and a60 == b60 and v60 is not None and v60 != b60:
                    self.t0_edge, self.t0_src = t, "060≠base"

        # 0x072 pulse tracking
        if self.in_window:
            pv72 = self.prev_vals.get(REL_072)
            v72  = self.now_vals.get(REL_072)
            was_on = (pv72 is not None and pv72 != 0)
            now_on = (v72  is not None and v72  != 0)

            if (not was_on) and now_on:
                self._t_072_on = t
                self._t_072_off = None
            elif was_on and (not now_on):
                if self._t_072_on is not None:
                    self._t_072_off = t

        # window end debounce
        if self.in_window:
            v60 = self.now_vals.get(REL_060)
            b60 = self._pref_baseline()
            v72 = self.now_vals.get(REL_072)
            v60_is_base = (v60 is not None and b60 is not None and v60 == b60)
            v72_zero = (v72 is None or v72 == 0)

            if v60_is_base and v72_zero:
                self._zero_frames_after_base += 1
            else:
                self._zero_frames_after_base = 0

            end_now = (self._zero_frames_after_base >= self.HYST_END_FRAMES)
        else:
            end_now = False

        if end_now:
            self.in_window = False
            self.t_window_end = t

            # Startup from 072 pulse
            startup_f = 0
            if self._t_072_on is not None and self._t_072_off is not None:
                startup_f = self._fr(self._t_072_off - self._t_072_on)
            elif self._t_072_on is not None:
                startup_f = self._fr(t - self._t_072_on)

            total_f = 0
            if self.t_window_start is not None and self.t_window_end is not None:
                total_f = self._fr(self.t_window_end - self.t_window_start)

            self.last_072_raw_frames = startup_f
            self.last_result = {
                "startup": startup_f,
                "total": total_f,
            }

    # ----- HUD -----
    def hud_gate_line(self, label: str) -> str:
        def fmt(rel: int, v: Optional[int]) -> str:
            return f"+0x{rel:03X}:{'--' if v is None else f'{v:02X}'}" + ("•" if v not in (None, 0) else " ")
        parts = "    ".join(fmt(r, self.now_vals.get(r)) for r in WATCH)
        return f"{label:<6} GATES: {parts}"

    def hud_move_line(self, label: str) -> Optional[str]:
        if self.in_window:
            v72 = self.now_vals.get(REL_072)
            if v72 and self._t_072_on is not None:
                live_raw = self._fr(time.time() - self._t_072_on)
                return f"{label:<6} LIVE: startup(raw) {live_raw}f"
            return f"{label:<6} LIVE: startup …"
        if self.last_result:
            r = self.last_result
            if self.last_072_raw_frames is not None:
                return f"{label:<6} LAST: startup {r['startup']}f  (072raw {self.last_072_raw_frames}f)  total {r['total']}f"
            return f"{label:<6} LAST: startup {r['startup']}f  total {r['total']}f"
        return None

# ================= Main =================
def main():
    print(Colors.BOLD + "Minimal HUD + pooled-life + gate watcher + STARTUP only" + Colors.RESET)
    hook()
    print("Hooked.")

    resolver = SlotResolver()
    last_base_by_slot: Dict[int, int] = {}
    y_off_by_base: Dict[int, int] = {}

    meter_prev = {"P1": 0, "P2": 0}
    meter_now  = {"P1": 0, "P2": 0}
    p1_c1_base: Optional[int] = None
    p2_c1_base: Optional[int] = None

    pool_prev: Dict[str, Optional[int]] = {}
    pool_now:  Dict[str, Optional[int]] = {}

    trackers: Dict[str, MoveTracker] = {
        nm: MoveTracker(side_hint=("P1" if nm.startswith("P1") else "P2")) for nm, _ in SLOTS
    }

    if SHOW_HUD:
        sys.stdout.write("\033[?25l"); sys.stdout.flush()
    _last_hud = 0.0
    HUD_REFRESH_INTERVAL = 1.0 / max(1, HUD_REFRESH_HZ)

    try:
        while True:
            # Resolve bases
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
                    pool_prev[name] = pool_now[name] = None  # reset delta

            # Leaders for meter reads
            for name, _, base, _ in resolved:
                if name == "P1-C1" and base: p1_c1_base = base
                if name == "P2-C1" and base: p2_c1_base = base

            # Meters
            meter_prev["P1"], meter_prev["P2"] = meter_now["P1"], meter_now["P2"]
            m1, m2 = read_meter(p1_c1_base), read_meter(p2_c1_base)
            if m1 is not None: meter_now["P1"] = m1
            if m2 is not None: meter_now["P2"] = m2

            # Snapshots + tracker updates
            info: Dict[str, Optional[Fighter]] = {}
            for name, _, base, _ in resolved:
                if base:
                    y = y_off_by_base.get(base, 0xF4)
                    f = read_fighter(base, y)
                    info[name] = f

                    pool_prev[name] = pool_now.get(name)
                    pool_now[name]  = _read_pool_byte(base)

                    # update gates for startup-only
                    trackers[name].update_vals(base)
                else:
                    info[name] = None
                    pool_prev[name] = pool_now[name] = None
                    trackers[name].update_vals(None)

            t = time.time()

            # Tick per slot
            for name, _, base, _ in resolved:
                trackers[name].tick(t)

            # HUD render
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

                # Extras: pooled-life + gates + startup-only
                extras: List[str] = []
                for nm, _ in SLOTS:
                    # pooled life
                    curp = pool_now.get(nm); prvp = pool_prev.get(nm)
                    if curp is None:
                        extras.append(f"{nm} pool(+0x{POOL_REL:03X}): --  Δ--")
                    else:
                        d = 0 if prvp is None else (curp - prvp)
                        extras.append(f"{nm} pool(+0x{POOL_REL:03X}): {curp:>3}  Δ{d:+}")

                for nm, _ in SLOTS:
                    extras.append(trackers[nm].hud_gate_line(nm))
                    mv = trackers[nm].hud_move_line(nm)
                    if mv: extras.append(mv)

                render_screen(lines, meter_summary, extras)

            time.sleep(INTERVAL)

    except KeyboardInterrupt:
        pass
    finally:
        if SHOW_HUD:
            sys.stdout.write("\033[?25h"); sys.stdout.flush()

if __name__ == "__main__":
    main()