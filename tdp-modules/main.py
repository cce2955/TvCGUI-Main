# main.py
# Minimal HUD + pooled-life + gate watcher + SAR (Startup/Active/Recovery)
# - t0 from edges ONLY: 058↑ / 059↑ / 060≠baseline (never 072)
# - ACTIVE if (064!=0) or (072==0x40)
# - WINDOW = (060!=baseline) or ACTIVE
# - WINDOW END when (060==baseline) AND (no ACTIVE) for HYST_END frames (debounce)

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

# Gate bytes we watch
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

# ================= SAR tracker =================
class MoveTracker:
    """
    Startup/Active/Recovery with:
      - t0 from edges ONLY: 058↑ / 059↑ / 060≠baseline (never 072)
      - ACTIVE if (064!=0) or (072==0x40)
      - WINDOW = (060!=baseline) or ACTIVE
      - WINDOW END when (060==baseline) AND (no ACTIVE) for HYST_END frames
    """
    HYST_END_FRAMES = 2  # debounce for window end

    def __init__(self, side_hint: str = ""):
        self.side_hint = side_hint
        self.baseline_060: Optional[int] = None

        self.prev_vals: Dict[int, Optional[int]] = {r: None for r in WATCH}
        self.now_vals:  Dict[int, Optional[int]] = {r: None for r in WATCH}

        # window state
        self.in_window: bool = False
        self.t_window_start: Optional[float] = None
        self.t_window_end: Optional[float] = None

        # earliest trigger (startup anchor) — edges only
        self.t0_edge: Optional[float] = None
        self.t0_src: Optional[str] = None

        # active spans
        self.active_spans: List[Tuple[float, float]] = []
        self._active_on_t: Optional[float] = None
        self._active_any_frame: bool = False

        # hysteresis
        self._no_active_frames: int = 0

        # last computed result
        self.last_result: Optional[Dict[str, int]] = None

    def _pref_baseline(self) -> Optional[int]:
        if self.baseline_060 is not None:
            return self.baseline_060
        if self.side_hint.startswith("P1"): return 0x04
        if self.side_hint.startswith("P2"): return 0x44
        return None

    def _rise(self, rel: int) -> bool:
        a, b = self.prev_vals.get(rel), self.now_vals.get(rel)
        return (a is not None and b is not None and a == 0 and b != 0)

    def _active_now(self) -> bool:
        v64 = self.now_vals.get(REL_064)
        v72 = self.now_vals.get(REL_072)
        return ((v64 is not None and v64 != 0) or (v72 == 0x40))

    def _window_now(self) -> bool:
        v60 = self.now_vals.get(REL_060)
        b60 = self._pref_baseline()
        if v60 is not None and b60 is not None and v60 != b60:
            return True
        # Allow early ACTIVE to hold window even if 060 hasn’t moved yet.
        return self._active_now()

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

        # learn/confirm baseline for 060
        v060 = self.now_vals.get(REL_060)
        if v060 is not None:
            pref = 0x04 if self.side_hint.startswith("P1") else (0x44 if self.side_hint.startswith("P2") else None)
            if self.baseline_060 is None:
                if pref is not None and v060 == pref:
                    self.baseline_060 = v060
            else:
                if pref is not None and v060 == pref:
                    self.baseline_060 = v060

    def _fr(self, dt: float) -> int:
        return max(0, int((dt * FPS) + 0.5))  # round half up

    def tick(self, t: float):
        window_now = self._window_now()
        active_now = self._active_now()

        # Window start
        if (not self.in_window) and window_now:
            self.in_window = True
            self.t_window_start = t
            self.t_window_end = None
            self.t0_edge = None
            self.t0_src = None
            self.active_spans.clear()
            self._active_on_t = None
            self._active_any_frame = False
            self._no_active_frames = 0

        # Establish t0 (edges ONLY)
        if self.in_window and self.t0_edge is None:
            if self._rise(REL_058):
                self.t0_edge, self.t0_src = t, "058↑"
            elif self._rise(REL_059):
                self.t0_edge, self.t0_src = t, "059↑"
            else:
                # 060 leaves baseline
                a60 = self.prev_vals.get(REL_060)
                b60 = self._pref_baseline()
                v60 = self.now_vals.get(REL_060)
                if a60 is not None and b60 is not None and a60 == b60 and v60 is not None and v60 != b60:
                    self.t0_edge, self.t0_src = t, "060≠base"

        # Active spans (064!=0 or 072==0x40)
        pv64 = self.prev_vals.get(REL_064)
        pv72 = self.prev_vals.get(REL_072)
        was_active = ((pv64 is not None and pv64 != 0) or (pv72 == 0x40))

        if (not was_active) and active_now:
            self._active_on_t = t
            self._active_any_frame = True
        if was_active and (not active_now):
            if self._active_on_t is not None:
                self.active_spans.append((self._active_on_t, t))
                self._active_on_t = None

        # Hysteresis for window end: count frames with no active while 060 is back to baseline
        v60 = self.now_vals.get(REL_060)
        b60 = self._pref_baseline()
        v60_is_base = (v60 is not None and b60 is not None and v60 == b60)
        if self.in_window:
            if (not active_now) and v60_is_base:
                self._no_active_frames += 1
            else:
                self._no_active_frames = 0

        # Window end (debounced)
        end_now = self.in_window and (self._no_active_frames >= self.HYST_END_FRAMES)
        if end_now:
            self.in_window = False
            self.t_window_end = t
            if self._active_on_t is not None:
                self.active_spans.append((self._active_on_t, t))
                self._active_on_t = None

            # Compute SAR
            total_f = 0
            startup_f = 0
            active_f = 0
            recovery_f = 0

            if self.t_window_start is not None and self.t_window_end is not None:
                total_f = self._fr(self.t_window_end - self.t_window_start)

            for a, b in self.active_spans:
                active_f += self._fr(b - a)
            if self._active_any_frame and active_f == 0:
                active_f = 1

            first_active_t = self.active_spans[0][0] if self.active_spans else None
            if self.t0_edge is not None and first_active_t is not None:
                startup_f = self._fr(first_active_t - self.t0_edge)
            elif first_active_t is not None and self.t_window_start is not None:
                startup_f = self._fr(first_active_t - self.t_window_start)
            else:
                startup_f = 0

            last_active_end_t = self.active_spans[-1][1] if self.active_spans else self.t_window_start
            if self.t_window_end is not None and last_active_end_t is not None:
                recovery_f = self._fr(self.t_window_end - last_active_end_t)

            # Consistency & clamp
            if startup_f + active_f > total_f:
                over = startup_f + active_f - total_f
                if active_f >= over:
                    active_f -= over
                else:
                    rem = over - active_f
                    active_f = 0
                    startup_f = max(0, startup_f - rem)

            recovery_f = max(0, total_f - startup_f - active_f)

            self.last_result = {
                "startup": startup_f,
                "active": active_f,
                "recovery": recovery_f,
                "total": total_f
            }

    def hud_gate_line(self, label: str) -> str:
        def fmt(rel: int, v: Optional[int]) -> str:
            return f"+0x{rel:03X}:{'--' if v is None else f'{v:02X}'}" + ("•" if v not in (None, 0) else " ")
        parts = "    ".join(fmt(r, self.now_vals.get(r)) for r in WATCH)
        return f"{label:<6} GATES: {parts}"

    def hud_move_line(self, label: str) -> Optional[str]:
        if self.in_window:
            # live active so far
            live_active = 0
            for a, b in self.active_spans:
                live_active += self._fr(b - a)
            if self._active_any_frame and live_active == 0:
                live_active = 1
            return f"{label:<6} LIVE: startup …  active {live_active}f  (recovery …)"
        if self.last_result:
            r = self.last_result
            return f"{label:<6} LAST: startup {r['startup']}f  active {r['active']}f  recovery {r['recovery']}f  total {r['total']}f"
        return None

# ================= Main =================
def main():
    print(Colors.BOLD + "Minimal HUD + pooled-life + gate watcher + SAR" + Colors.RESET)
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

                    # update gates for SAR
                    trackers[name].update_vals(base)
                else:
                    info[name] = None
                    pool_prev[name] = pool_now[name] = None
                    trackers[name].update_vals(None)

            t = time.time()

            # Tick SAR per slot
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

                # Extras: pooled-life + gates + SAR
                extras: List[str] = []
                for nm, _ in SLOTS:
                    # pooled life
                    curp = pool_now.get(nm); prvp = pool_prev.get(nm)
                    if curp is None:
                        extras.append(f"{nm} pool(+0x{POOL_REL:03X}): --  Δ--")
                    else:
                        d = 0 if prvp is None else (curp - prvp)
                        extras.append(f"{nm} pool(+0x{POOL_REL:03X}): {curp:>3}  Δ{d:+}")

                # Gates + SAR lines in order
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