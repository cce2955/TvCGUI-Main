# main.py
# HUD + pooled-life + expanded 0x072 window (PRE / DURING / AFTER)
# Attack ID used only for labeling; startup/active/recovery from wire lines.
# - Tracks 0x050..0x080 around 0x072 pulses
# - Captures attack id/sub/name every frame (same slot) and summarizes DURING
# - Exports PRE/DURING/AFTER stats + AID metadata + phase segmentation to CSV (+XLSX if pandas available)

from __future__ import annotations
import time, sys, csv, os
from typing import Dict, Optional, Tuple, List
from collections import deque, Counter

from colors import Colors
from config import SHOW_HUD, HUD_REFRESH_HZ, INTERVAL
from constants import SLOTS
from dolphin_io import hook, rbytes
from hud import fmt_line, render_screen
from meter import read_meter, drop_meter_cache_for_base
from models import Fighter, read_fighter
from resolver import SlotResolver, pick_posy_off_no_jump
from attack_ids import read_attack_ids  # (id:int?, sub:int?, name:str)

# ================= Tunables =================
FPS = 60.0
POOL_REL = 0x02E

# Scope we scan around 0x072. You can widen/narrow this safely.
REL_SCOPE_START = 0x050
REL_SCOPE_END   = 0x080   # inclusive
REL_SCOPE = tuple(range(REL_SCOPE_START, REL_SCOPE_END + 1))

REL_052 = 0x052
REL_053 = 0x053
REL_056 = 0x056
REL_057 = 0x057
REL_058 = 0x058
REL_059 = 0x059
REL_05B = 0x05B
REL_072 = 0x072

# Window sizes (in frames)
PRE_FRAMES   = 12   # frames just before 0x072 turns on
POST_FRAMES  = 24   # frames after 0x072 turns off

# Export settings
EXPORT_BASENAME = "tvc_sessions"
EXPORT_DIR = "."  # current folder

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

def _fr(dt: float) -> int:
    return max(0, int((dt * FPS) + 0.5))

def _compute_stats(series: List[int]) -> Tuple[int, int, int]:
    """
    Returns (flips, nz_count, first_nz_index) over a byte-valued series.
    """
    if not series:
        return (0, 0, -1)
    flips = 0
    prev = series[0]
    for v in series[1:]:
        if v != prev:
            flips += 1
        prev = v
    nz = sum(1 for v in series if v != 0)
    first = -1
    for i, v in enumerate(series):
        if v != 0:
            first = i
            break
    return (flips, nz, first)

def _hot_list(stats_by_rel: Dict[int, Tuple[int,int,int]], topk: int = 5) -> str:
    # Sort by nz desc, then by first asc (earlier = hotter), then rel asc
    items = sorted(
        stats_by_rel.items(),
        key=lambda kv: (-kv[1][1], kv[1][2] if kv[1][2] != -1 else 9999, kv[0])
    )
    items = [f"+0x{rel:03X}:{nz}" for rel, (_, nz, _) in items[:topk] if nz > 0]
    return ";".join(items)

def _aid_mode(series: List[Tuple[Optional[int], Optional[int], str]]):
    """
    Given [(id, sub, name), ...] DURING series, return:
    - mode_id, mode_sub, mode_name (most frequent nonzero id)
    - first_id, first_sub, first_name (first nonzero id)
    - flips, nz_count, first_idx (computed on 'id' stream)
    """
    ids = [i or 0 for (i, _, _) in series]
    flips, nz, first_idx = _compute_stats(ids)
    # first nonzero
    first_id = first_sub = None
    first_name = ""
    for (i, s, n) in series:
        if i and i != 0:
            first_id, first_sub, first_name = i, s, n or ""
            break
    # mode over nonzero
    nonzero_ids = [(i, s, n) for (i, s, n) in series if i and i != 0]
    mode_id = mode_sub = None
    mode_name = ""
    if nonzero_ids:
        cnt = Counter(i for (i, _, _) in nonzero_ids)
        mode_id, _ = cnt.most_common(1)[0]
        for (i, s, n) in reversed(nonzero_ids):
            if i == mode_id:
                mode_sub, mode_name = s, (n or "")
                break
    return {
        "mode_id": mode_id, "mode_sub": mode_sub, "mode_name": mode_name or "",
        "first_id": first_id, "first_sub": first_sub, "first_name": first_name or "",
        "flips": flips, "nz": nz, "first_idx": first_idx
    }

def _segment_phases(during_series: Dict[int, List[int]]) -> dict:
    """
    Phase segmentation from wire signals (no guessing):
      - Startup: from first lead activity to first hit activity
      - Active: contiguous hit activity span(s)
      - Recovery: remainder of DURING after last hit
    Lead lines: 0x052, 0x053
    Hit  lines: 0x056, 0x057, 0x058
    All indices are DURING-frame coordinates (0..L-1).
    """
    L = max((len(during_series.get(REL_072, [])) or 0), 0)

    def first_nz(mask):
        for i, v in enumerate(mask):
            if v:
                return i
        return -1

    # Build masks across DURING
    lead_mask = [0]*L
    hit_mask  = [0]*L
    for rel in (REL_052, REL_053):
        s = during_series.get(rel, [])
        for i in range(min(L, len(s))):
            if s[i] != 0: lead_mask[i] = 1
    for rel in (REL_056, REL_057, REL_058):
        s = during_series.get(rel, [])
        for i in range(min(L, len(s))):
            if s[i] != 0: hit_mask[i] = 1

    lf = first_nz(lead_mask)
    hf = first_nz(hit_mask)

    hl = -1
    for i in range(L-1, -1, -1):
        if hit_mask[i]:
            hl = i
            break

    if hf == -1:
        # No active window detected
        t0 = 0 if lf == -1 else lf
        startup = max(0, L - t0)
        return {
            "during_len": L,
            "startup_frames": startup,
            "active_frames": 0,
            "recovery_frames": 0,
            "first_hit_idx": -1,
            "last_hit_idx": -1,
            "lead_first_idx": lf
        }

    # Startup begins at min(lf, hf) if lead present, else 0..hf
    t0 = 0 if lf == -1 else min(lf, hf)
    startup = max(0, hf - t0)
    active  = (hl - hf + 1) if hl >= hf else 0
    recov   = max(0, L - (t0 + startup + active))
    return {
        "during_len": L,
        "startup_frames": startup,
        "active_frames":  active,
        "recovery_frames": recov,
        "first_hit_idx":  hf,
        "last_hit_idx":   hl,
        "lead_first_idx": lf
    }

# ================= Startup/Active tracker based on 0x072 =================
class MoveTracker:
    """
    Tracks sessions keyed by 0x072 pulse:
      - PRE: last PRE_FRAMES frames before 0x072 rises
      - DURING: frames while 0x072 != 0
      - AFTER: next POST_FRAMES frames after 0x072 returns to 0
    Also computes per-address stats, “attack-like” tag, Attack-ID summary,
    and wire-driven phase segmentation (startup/active/recovery).
    """

    def __init__(self, slot_name: str, side_hint: str = ""):
        self.slot_name = slot_name
        self.side_hint = side_hint

        # rolling buffers of the last PRE_FRAMES values (for every rel in scope)
        self._pre_ring: Dict[int, deque] = {rel: deque(maxlen=PRE_FRAMES) for rel in REL_SCOPE}

        # rolling PRE for attack-id as well
        self._aid_pre_ring: deque = deque(maxlen=PRE_FRAMES)

        # live/current byte snapshot
        self._now: Dict[int, Optional[int]] = {rel: None for rel in REL_SCOPE}
        self._aid_now: Tuple[Optional[int], Optional[int], str] = (None, None, "")

        # session state
        self.in_session: bool = False
        self.session_idx: int = 0
        self._t_start: Optional[float] = None
        self._t_end: Optional[float] = None

        # per-session raw collections
        self._pre_series: Dict[int, List[int]] = {}
        self._during_series: Dict[int, List[int]] = {}
        self._after_series: Dict[int, List[int]] = {}
        self._after_left: int = 0

        # Attack-ID series per phase
        self._aid_pre: List[Tuple[Optional[int], Optional[int], str]] = []
        self._aid_during: List[Tuple[Optional[int], Optional[int], str]] = []
        self._aid_after: List[Tuple[Optional[int], Optional[int], str]] = []

        # HUD live length while 0x072 on
        self._t_072_on: Optional[float] = None

        # export accumulator callback (set externally)
        self._export_cb = None

    def set_export_cb(self, fn):
        self._export_cb = fn

    # ---- IO snapshot ----
    def update_vals(self, base: Optional[int]):
        # bytes
        if base:
            for rel in REL_SCOPE:
                v = _rd8(base, rel)
                v = 0 if v is None else v
                self._now[rel] = v
                self._pre_ring[rel].append(v)
        else:
            for rel in REL_SCOPE:
                self._now[rel] = 0
                self._pre_ring[rel].append(0)

        # attack id (same slot)
        if base:
            aid, sub, name = read_attack_ids(base)  # (id:int?, sub:int?, name:str)
            self._aid_now = (aid, sub, name or "")
        else:
            self._aid_now = (None, None, "")
        self._aid_pre_ring.append(self._aid_now)

    # ---- tick ----
    def tick(self, t: float):
        v72 = self._now.get(REL_072, 0)
        on = (v72 != 0)

        # session start
        if (not self.in_session) and on:
            self.in_session = True
            self.session_idx += 1
            self._t_start = t
            self._t_end = None
            self._t_072_on = t

            # snapshot PRE
            self._pre_series  = {rel: list(self._pre_ring[rel]) for rel in REL_SCOPE}
            self._aid_pre     = list(self._aid_pre_ring)

            # start DURING
            self._during_series = {rel: [] for rel in REL_SCOPE}
            self._aid_during    = []

            # reset AFTER
            self._after_series = {rel: [] for rel in REL_SCOPE}
            self._aid_after    = []
            self._after_left = 0

        # record DURING
        if self.in_session and on:
            for rel in REL_SCOPE:
                self._during_series[rel].append(self._now[rel])
            self._aid_during.append(self._aid_now)

        # 072 turned off → begin AFTER
        if self.in_session and (not on) and self._after_left == 0 and self._t_072_on is not None:
            self._after_left = POST_FRAMES
            self._t_072_on = None
            self._t_end = t  # wall end (end of DURING)

        # record AFTER
        if self.in_session and self._after_left > 0:
            for rel in REL_SCOPE:
                self._after_series[rel].append(self._now[rel])
            self._aid_after.append(self._aid_now)
            self._after_left -= 1

            # session done once AFTER collected
            if self._after_left == 0:
                self._finalize_session()

    def _finalize_session(self):
        try:
            wall_start = self._t_start if self._t_start is not None else time.time()
            wall_end   = self._t_end   if self._t_end   is not None else time.time()

            # DURING/AFTER stats for each rel
            D_stats: Dict[int, Tuple[int,int,int]] = {}
            A_stats: Dict[int, Tuple[int,int,int]] = {}
            for rel in REL_SCOPE:
                D_stats[rel] = _compute_stats(self._during_series.get(rel, []))
                A_stats[rel] = _compute_stats(self._after_series.get(rel, []))

            # DURING length from 0x072
            during_len = max(len(self._during_series.get(REL_072, [])), 0)
            post_len   = max(len(self._after_series.get(REL_072, [])), 0)

            # attack-like tag (0x058 & 0x059 line up with 0x072 length)
            tol = 1
            d58 = D_stats.get(REL_058, (0,0,-1))
            d59 = D_stats.get(REL_059, (0,0,-1))
            attack_like = (
                d58[2] == 0 and d59[2] == 0 and
                abs(d58[1] - during_len) <= tol and
                abs(d59[1] - during_len) <= tol
            )

            # hot lists
            hot_during = _hot_list(D_stats, topk=5)
            hot_after  = _hot_list(A_stats, topk=5)

            # Attack ID summaries (label only)
            aid_pre_first  = _aid_mode(self._aid_pre)
            aid_dur_stats  = _aid_mode(self._aid_during)
            aid_post_first = _aid_mode(self._aid_after)

            # ---- Wire-driven phase segmentation ----
            seg = _segment_phases(self._during_series)
            # (we keep 'during_len' from 0x072; seg["during_len"] should match)
            startup_frames   = seg["startup_frames"]
            active_frames    = seg["active_frames"]
            recovery_frames  = seg["recovery_frames"]
            first_hit_idx    = seg["first_hit_idx"]
            last_hit_idx     = seg["last_hit_idx"]
            lead_first_idx   = seg["lead_first_idx"]

            # Row assembly
            row: Dict[str, object] = {
                "slot": self.slot_name,
                "session_idx": self.session_idx,
                "wall_start": wall_start,
                "wall_end": wall_end,
                "len_072_frames": during_len,
                "post_frames": post_len,
                "attack_like": int(attack_like),
                "hot_during": hot_during,
                "hot_after": hot_after,

                # DURING coordinate endpoints (for convenience)
                "t0": 0,
                "t1": max(during_len - 1, -1),

                # ----- Phase segmentation results -----
                "startup_frames":  startup_frames,
                "active_frames":   active_frames,
                "recovery_frames": recovery_frames,
                "first_hit_idx":   first_hit_idx,
                "last_hit_idx":    last_hit_idx,
                "lead_first_idx":  lead_first_idx,

                # --- Attack-ID DURING summary (mode + first) ---
                "attacker_id_dec": (aid_dur_stats["mode_id"] or ""),
                "attacker_id_hex": (hex(aid_dur_stats["mode_id"]) if aid_dur_stats["mode_id"] is not None else ""),
                "attacker_sub":    (aid_dur_stats["mode_sub"] if aid_dur_stats["mode_sub"] is not None else ""),
                "attacker_move":   aid_dur_stats["mode_name"],

                "aid_flips":       aid_dur_stats["flips"],
                "aid_nz":          aid_dur_stats["nz"],
                "aid_first_idx":   aid_dur_stats["first_idx"],

                "aid_first_dec": (aid_dur_stats["first_id"] or ""),
                "aid_first_hex": (hex(aid_dur_stats["first_id"]) if aid_dur_stats["first_id"] is not None else ""),
                "aid_first_sub": (aid_dur_stats["first_sub"] if aid_dur_stats["first_sub"] is not None else ""),
                "aid_first_name": aid_dur_stats["first_name"],

                # PRE/AFTER firsts (debug alignment)
                "aid_pre_first_hex":  (hex(aid_pre_first["first_id"]) if aid_pre_first["first_id"] is not None else ""),
                "aid_post_first_hex": (hex(aid_post_first["first_id"]) if aid_post_first["first_id"] is not None else ""),
            }

            # PRE stats
            for rel in REL_SCOPE:
                pf = _compute_stats(self._pre_series.get(rel, []))
                row[f"P_flips_0x{rel:03X}"] = pf[0]
                row[f"P_nz_0x{rel:03X}"]    = pf[1]
                row[f"P_first_0x{rel:03X}"] = pf[2]

            # DURING stats
            for rel in REL_SCOPE:
                df = D_stats[rel]
                row[f"D_flips_0x{rel:03X}"] = df[0]
                row[f"D_nz_0x{rel:03X}"]    = df[1]
                row[f"D_first_0x{rel:03X}"] = df[2]

            # AFTER stats
            for rel in REL_SCOPE:
                af = A_stats[rel]
                row[f"A_flips_0x{rel:03X}"] = af[0]
                row[f"A_nz_0x{rel:03X}"]    = af[1]
                row[f"A_first_0x{rel:03X}"] = af[2]

            # Export
            if self._export_cb:
                self._export_cb(row)

        finally:
            # reset session state
            self.in_session = False
            self._t_start = None
            self._t_end = None
            self._t_072_on = None
            self._pre_series = {}
            self._during_series = {}
            self._after_series = {}
            self._aid_pre = []
            self._aid_during = []
            self._aid_after = []
            self._after_left = 0

    # ----- HUD snippets -----
    def hud_gate_line(self) -> str:
        # show a compact HOT readout for the current frame (subset)
        hot_now = []
        for rel in (REL_058, REL_059, 0x060, REL_072):
            v = self._now.get(rel, 0)
            hot_now.append(f"+0x{rel:03X}:{v:02X}" + ("•" if v else " "))
        # attach live AID (current id short)
        aid, sub, name = self._aid_now
        aid_str = f"AID:{aid if aid is not None else '--'}"
        return f"{self.slot_name:<6} HOT: " + "  ".join(hot_now) + f"   {aid_str}"

    def hud_move_line(self) -> Optional[str]:
        if self.in_session and self._t_072_on is not None:
            live = _fr(time.time() - self._t_072_on)
            return f"{self.slot_name:<6} 0x072 LIVE: {live}f"
        if not self.in_session and self._t_end is not None and self._t_start is not None:
            dur = len(self._during_series.get(REL_072, []))
            return f"{self.slot_name:<6} LAST 0x072: {dur}f"
        return None


# =============== Exporter ===============
class SessionExporter:
    def __init__(self, basename: str = EXPORT_BASENAME, out_dir: str = EXPORT_DIR):
        self.basename = basename
        self.out_dir = out_dir

        # One timestamp per process/run. Safe for Windows filenames.
        self.run_id = time.strftime("%Y%m%d-%H%M%S")

        # Example: tvc_sessions.20251021-153422.csv / .xlsx
        self.csv_path = os.path.join(out_dir, f"{basename}.{self.run_id}.csv")
        self.xlsx_path = os.path.join(out_dir, f"{basename}.{self.run_id}.xlsx")

        self._fieldnames: List[str] = []
        self._csv_ready = False
        self._rows_buffer: List[Dict[str, object]] = []

        # Try pandas/xlsx
        self._pandas_ok = False
        try:
            import pandas as pd  # noqa: F401
            self._pandas_ok = True
        except Exception:
            self._pandas_ok = False

        # ensure directory
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            pass

        print(f"Exporting to:\n  CSV : {self.csv_path}\n  XLSX: {self.xlsx_path if self._pandas_ok else '(pandas/openpyxl not available)'}")

    def _init_csv(self, row: Dict[str, object]):
        if not self._csv_ready:
            self._fieldnames = list(row.keys())
            with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=self._fieldnames)
                w.writeheader()
            self._csv_ready = True

    def add_row(self, row: Dict[str, object]):
        self._init_csv(row)
        with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=self._fieldnames)
            w.writerow(row)
        self._rows_buffer.append(row)
        if len(self._rows_buffer) >= 50:
            self.flush_xlsx()

    def flush_xlsx(self):
        if not self._pandas_ok or not self._rows_buffer:
            return
        try:
            import pandas as pd
            df = pd.DataFrame(self._rows_buffer)
            df.to_excel(self.xlsx_path, index=False)
            self._rows_buffer.clear()
        except Exception:
            pass


# ================= Main =================
def main():
    print(Colors.BOLD + "HUD + pooled-life + 0x072 session tracker + Attack-ID labeling + Wire segmentation" + Colors.RESET)
    hook()
    print("Hooked.")

    exporter = SessionExporter()

    resolver = SlotResolver()
    last_base_by_slot: Dict[int, int] = {}
    y_off_by_base: Dict[int, int] = {}

    meter_prev = {"P1": 0, "P2": 0}
    meter_now  = {"P1": 0, "P2": 0}
    p1_c1_base: Optional[int] = None
    p2_c1_base: Optional[int] = None

    pool_prev: Dict[str, Optional[int]] = {}
    pool_now:  Dict[str, Optional[int]] = {}

    trackers: Dict[str, MoveTracker] = {}
    for nm, _ in SLOTS:
        tr = MoveTracker(nm, side_hint=("P1" if nm.startswith("P1") else "P2"))
        tr.set_export_cb(exporter.add_row)
        trackers[nm] = tr

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

                    trackers[name].update_vals(base)
                else:
                    info[name] = None
                    pool_prev[name] = pool_now[name] = None
                    trackers[name].update_vals(None)

            t = time.time()

            # Tick per slot
            for name, _, _base, _ in resolved:
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

                extras: List[str] = []
                # pooled life
                for nm, _ in SLOTS:
                    curp = pool_now.get(nm); prvp = pool_prev.get(nm)
                    if curp is None:
                        extras.append(f"{nm} pool(+0x{POOL_REL:03X}): --  Δ--")
                    else:
                        d = 0 if prvp is None else (curp - prvp)
                        extras.append(f"{nm} pool(+0x{POOL_REL:03X}): {curp:>3}  Δ{d:+}")

                # HOT now + brief move line + AID
                for nm, _ in SLOTS:
                    extras.append(trackers[nm].hud_gate_line())
                    mv = trackers[nm].hud_move_line()
                    if mv: extras.append(mv)

                render_screen(lines, meter_summary, extras)

            time.sleep(INTERVAL)

    except KeyboardInterrupt:
        pass
    finally:
        if SHOW_HUD:
            sys.stdout.write("\033[?25h"); sys.stdout.flush()
        # final XLSX flush
        try:
            exporter.flush_xlsx()
        except Exception:
            pass


if __name__ == "__main__":
    # Safety: ensure Colors has attributes we reference
    for attr, fallback in (("CYAN", "\033[36m"), ("DIM", "\033[2m")):
        if not hasattr(Colors, attr):
            setattr(Colors, attr, fallback)
    main()