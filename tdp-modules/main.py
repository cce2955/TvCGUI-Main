# main.py
# Minimal HUD + pooled-life watcher (+0x02E) with cached micro-scan & lingering events
# + Activity/lock timing via +0x1EA/+0x1EE, and X(+0x08E) + Z?(+0x0B2) anomaly tracking

from __future__ import annotations
import time, sys, os, csv
from collections import deque
from typing import Dict, Optional, Tuple, List

from colors import Colors
from config import SHOW_HUD, HUD_REFRESH_HZ, INTERVAL
from constants import SLOTS
from dolphin_io import hook, rbytes, rd32
from hud import fmt_line, render_screen
from meter import read_meter, drop_meter_cache_for_base
from models import Fighter, read_fighter
from resolver import SlotResolver, pick_posy_off_no_jump

# ---------- Tunables ----------
POOL_REL = 0x02E
POOL_DROP_MIN = 2
HP_TOLERANCE = 1

SCAN_LO = 0x000
SCAN_HI = 0x200      # expand to 0x400 if you want
SCAN_STRIDES = (1, 2, 4)

POST_SAMPLES = 5
POST_DT = 0.05

# Activity / "lock" detection: action starts when either flips to 1; ends when both 0
LOCK_BYTES = (0x1EA, 0x1EE)

# X-axis and Z anomaly words
XWORD_REL = 0x08E
ZWORD_REL = 0x0B2   # anomaly tracker; label as "Z?" for now

# Event buffer (linger)
EV_BUF_MAX = 30
EV_TTL_S   = 45.0

# Show only latched + pulsed by default (set to True to dump everything)
SHOW_NOISY = False

WRITE_CSV = False
FPS = 60.0  # for converting seconds to frames on lock timings

# ---------- Event buffer ----------
class EvBuf:
    def __init__(self, cap=EV_BUF_MAX, ttl_s=EV_TTL_S):
        self.q = deque()
        self.cap = cap
        self.ttl = ttl_s
    def push(self, t: float, text: str):
        self.q.append((t, text))
        while len(self.q) > self.cap:
            self.q.popleft()
    def render_lines(self, now_t: float) -> List[str]:
        out = []
        # drop expired
        while self.q and now_t - self.q[0][0] > self.ttl:
            self.q.popleft()
        for _, s in list(self.q):
            out.append(s)
        return out

EV = EvBuf()

# ---------- I/O helpers ----------
def _fighter_to_blk(f: Optional[Fighter]) -> Optional[dict]:
    if f is None: return None
    return {"base": f.base, "max": f.max, "cur": f.cur, "aux": f.aux,
            "id": f.id, "name": f.name, "x": f.x, "y": f.y, "last": f.last}

def _rd8(base: Optional[int], rel: int) -> Optional[int]:
    if not base: return None
    raw = rbytes(base + rel, 1)
    if not raw or len(raw) != 1: return None
    return int(raw[0])

def _rd16_be(base: Optional[int], rel: int) -> Optional[int]:
    if not base: return None
    raw = rbytes(base + rel, 2)
    if not raw or len(raw) != 2: return None
    return (raw[0] << 8) | raw[1]

def _read_pool_byte(base: Optional[int]) -> Optional[int]:
    return _rd8(base, POOL_REL)

def _read_span(base: int, stride: int, lo: int, hi: int) -> List[Optional[int]]:
    if stride == 4:
        out: List[Optional[int]] = []
        for rel in range(lo, hi, 4):
            v = rd32(base + rel)
            out.append(None if v is None else (v & 0xFFFFFFFF))
        return out
    raw = rbytes(base + lo, hi - lo)
    if not raw: return []
    if stride == 2:
        if len(raw) < 2: return []
        return [((raw[i] << 8) | raw[i+1]) for i in range(0, len(raw) - (len(raw) % 2), 2)]
    return list(raw)

def _diff_span(arrA: List[Optional[int]], arrB: List[Optional[int]], stride: int, lo: int) -> List[Tuple[int,int,int,int]]:
    diffs: List[Tuple[int,int,int,int]] = []
    if not arrA or not arrB: return diffs
    n = min(len(arrA), len(arrB))
    for i in range(n):
        a, b = arrA[i], arrB[i]
        if a is None or b is None: continue
        if a != b:
            rel = lo + i * stride
            diffs.append((rel, stride, int(a), int(b)))
    return diffs

# ---------- scan & classify ----------
def _classify_posts(b: int, posts: List[Optional[int]]) -> str:
    """Return 'latched' (all == b), 'pulsed' (first!=b then revert), 'noisy' (keeps changing)."""
    xs = [v for v in posts if v is not None]
    if not xs: return "noisy"
    if all(v == b for v in xs):
        return "latched"
    if xs[0] != b and xs[-1] == b:
        return "pulsed"
    return "noisy"

def scan_with_cached_prepost(
    t: float, slot: str, base: int,
    cached_pre: Dict[int, List[Optional[int]]],
    cached_post: Dict[int, List[Optional[int]]],
    csv_writer: Optional[csv.writer]
) -> None:
    diffs: List[Tuple[int,int,int,int]] = []
    for s in SCAN_STRIDES:
        diffs.extend(_diff_span(cached_pre.get(s, []), cached_post.get(s, []), s, SCAN_LO))

    if not diffs:
        EV.push(t, f"[{t:10.3f}] SCAN {slot} base=0x{base:08X} no flips")
        return

    posts_by_key: Dict[Tuple[int,int], List[Optional[int]]] = { (rel,stride): [] for (rel, stride, _, _) in diffs }
    for _ in range(POST_SAMPLES):
        time.sleep(POST_DT)
        for s in SCAN_STRIDES:
            arr = _read_span(base, s, SCAN_LO, SCAN_HI)
            if not arr: continue
            for (rel, stride, _, _) in diffs:
                if stride != s: continue
                idx = (rel - SCAN_LO) // stride
                if 0 <= idx < len(arr):
                    v = arr[idx]
                    posts_by_key[(rel,stride)].append(None if v is None else int(v))

    lines = []
    for (rel, stride, a, b) in diffs:
        posts = posts_by_key.get((rel, stride), [])
        cls = _classify_posts(b, posts)
        if (cls == "noisy") and not SHOW_NOISY:
            continue
        post_txt = ",".join("-" if v is None else str(int(v)) for v in posts)
        lines.append(f"  {cls.upper():7s} +0x{rel:03X}/s{stride}: {a} -> {b} | posts:{post_txt}")
        if csv_writer:
            row = [f"{t:.6f}", slot, f"0x{base:08X}", f"0x{rel:03X}", stride, a, b] + [
                ("" if v is None else v) for v in posts
            ]
            csv_writer.writerow(row)

    if lines:
        EV.push(t, f"[{t:10.3f}] SCAN {slot} base=0x{base:08X} flips:")
        for ln in lines:
            EV.push(t, ln)

def _bits8(v: Optional[int]) -> str:
    return f"{v:08b}" if v is not None else "--"

# ---------- Main ----------
def main():
    print(Colors.BOLD + "Minimal HUD + pooled-life + micro-scan + LOCK HUD + X/Z anomaly" + Colors.RESET)
    hook()
    print("Hooked.")

    csv_fh = None
    csv_w  = None
    if WRITE_CSV:
        logdir = os.path.join("logs", time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(logdir, exist_ok=True)
        csv_fh = open(os.path.join(logdir, "baroque_scans.csv"), "w", newline="")
        csv_w  = csv.writer(csv_fh)
        header = ["t","slot","base_hex","rel_off_hex","stride","pre","post"] + [f"post{k+1}" for k in range(POST_SAMPLES)]
        csv_w.writerow(header)

    resolver = SlotResolver()
    last_base_by_slot: Dict[int, int] = {}
    y_off_by_base: Dict[int, int] = {}

    meter_prev = {"P1": 0, "P2": 0}
    meter_now  = {"P1": 0, "P2": 0}
    p1_c1_base: Optional[int] = None
    p2_c1_base: Optional[int] = None

    pool_prev: Dict[str, Optional[int]] = {}
    pool_now:  Dict[str, Optional[int]] = {}
    hp_prev:   Dict[str, Optional[int]] = {}
    hp_now:    Dict[str, Optional[int]] = {}

    scan_prev: Dict[str, Dict[int, List[Optional[int]]]] = {}
    scan_now:  Dict[str, Dict[int, List[Optional[int]]]] = {}

    # Focused flag/word snapshots
    wb_prev: Dict[str, Dict[int, Optional[int]]] = {}
    wb_now:  Dict[str, Dict[int, Optional[int]]] = {}

    # X/Z trackers
    xw_prev: Dict[str, Optional[int]] = {}
    xw_now:  Dict[str, Optional[int]] = {}
    xw_base: Dict[str, Optional[int]] = {}
    zw_prev: Dict[str, Optional[int]] = {}
    zw_now:  Dict[str, Optional[int]] = {}
    zw_base: Dict[str, Optional[int]] = {}

    # Lock timing
    lock_prev: Dict[str, Optional[bool]] = {}
    lock_now:  Dict[str, Optional[bool]] = {}
    lock_t0:   Dict[str, Optional[float]] = {}

    if SHOW_HUD:
        sys.stdout.write("\033[?25l"); sys.stdout.flush()
    _last_hud = 0.0
    HUD_REFRESH_INTERVAL = 1.0 / max(1, HUD_REFRESH_HZ)

    try:
        while True:
            resolved: List[Tuple[str, int, Optional[int], bool]] = []
            for name, slot in SLOTS:
                base, changed = resolver.resolve_base(slot)
                resolved.append((name, slot, base, changed))

            for name, slot, base, changed in resolved:
                if base and last_base_by_slot.get(slot) != base:
                    last_base_by_slot[slot] = base
                    drop_meter_cache_for_base(base)
                    y_off_by_base[base] = pick_posy_off_no_jump(base)
                    pool_prev[name] = pool_now[name] = None
                    hp_prev[name]   = hp_now[name]   = None
                    scan_prev[name] = {}
                    scan_now[name]  = {}
                    wb_prev[name]   = {}
                    wb_now[name]    = {}
                    xw_prev[name] = xw_now[name] = xw_base[name] = None
                    zw_prev[name] = zw_now[name] = zw_base[name] = None
                    lock_prev[name] = lock_now[name] = None
                    lock_t0[name] = None

            for name, slot, base, _ in resolved:
                if name == "P1-C1" and base: p1_c1_base = base
                if name == "P2-C1" and base: p2_c1_base = base

            meter_prev["P1"], meter_prev["P2"] = meter_now["P1"], meter_now["P2"]
            m1, m2 = read_meter(p1_c1_base), read_meter(p2_c1_base)
            if m1 is not None: meter_now["P1"] = m1
            if m2 is not None: meter_now["P2"] = m2

            info: Dict[str, Optional[Fighter]] = {}
            for name, slot, base, _ in resolved:
                if base:
                    # rotate scan caches
                    scan_prev[name] = scan_now.get(name, {})
                    now_bucket: Dict[int, List[Optional[int]]] = {}
                    for s in SCAN_STRIDES:
                        now_bucket[s] = _read_span(base, s, SCAN_LO, SCAN_HI)
                    scan_now[name] = now_bucket

                    # fighter & HP
                    y = y_off_by_base.get(base, 0xF4)
                    f = read_fighter(base, y)
                    info[name] = f

                    # pooled life
                    pool_prev[name] = pool_now.get(name)
                    pool_now[name]  = _read_pool_byte(base)

                    # HP
                    hp_prev[name] = hp_now.get(name)
                    hp_now[name]  = (f.cur if f else None)

                    # activity/lock bytes
                    prev_bytes = wb_now.get(name, {})
                    wb_prev[name] = dict(prev_bytes)
                    cur_bytes: Dict[int, Optional[int]] = {}
                    for rel in LOCK_BYTES:
                        cur_bytes[rel] = _rd8(base, rel)
                    wb_now[name] = cur_bytes

                    # derive lock state: ON if any of the lock bytes is 1
                    prev_lock = lock_now.get(name)
                    now_lock = None
                    if len(cur_bytes) == len(LOCK_BYTES):
                        now_lock = any((cur_bytes.get(r, 0) or 0) == 1 for r in LOCK_BYTES)
                    lock_prev[name] = prev_lock
                    lock_now[name] = now_lock

                    # X/Z words
                    xw_prev[name] = xw_now.get(name)
                    zw_prev[name] = zw_now.get(name)
                    xw = _rd16_be(base, XWORD_REL)
                    zw = _rd16_be(base, ZWORD_REL)
                    xw_now[name] = xw
                    zw_now[name] = zw
                    if xw_base.get(name) is None and xw is not None:
                        xw_base[name] = xw
                    if zw_base.get(name) is None and zw is not None:
                        zw_base[name] = zw
                else:
                    info[name] = None
                    pool_prev[name] = pool_now[name] = None
                    hp_prev[name]   = hp_now[name]   = None
                    scan_prev[name] = {}
                    scan_now[name]  = {}
                    wb_prev[name]   = {}
                    wb_now[name]    = {}
                    xw_prev[name] = xw_now[name] = xw_base[name] = None
                    zw_prev[name] = zw_now[name] = zw_base[name] = None
                    lock_prev[name] = lock_now[name] = None
                    lock_t0[name] = None

            t = time.time()

            # Detect pooled-life (“Baroque spend”) proxy and scan around it
            for name, slot, base, _ in resolved:
                if not base: continue
                cur_pool = pool_now.get(name); prv_pool = pool_prev.get(name)
                cur_hp   = hp_now.get(name);   prv_hp   = hp_prev.get(name)
                if cur_pool is None or prv_pool is None: continue
                pool_delta = cur_pool - prv_pool
                hp_delta = None if (cur_hp is None or prv_hp is None) else (cur_hp - prv_hp)

                if pool_delta <= -POOL_DROP_MIN and (hp_delta is None or hp_delta >= -HP_TOLERANCE):
                    EV.push(t, f"[{t:10.3f}] BAROQUE SPEND slot={name} base=0x{base:08X} pool {prv_pool}->{cur_pool} (Δ{pool_delta}) hpΔ={hp_delta if hp_delta is not None else 'n/a'}")
                    scan_with_cached_prepost(t, name, base, scan_prev.get(name, {}), scan_now.get(name, {}), csv_w)

            # Lock timing transitions
            for name, slot, base, _ in resolved:
                if not base: continue
                prev = lock_prev.get(name)
                cur  = lock_now.get(name)
                if cur is None: continue
                if prev is None:
                    # initialize
                    lock_t0[name] = (t if cur else None)
                elif prev != cur:
                    if cur:
                        # lock just turned ON
                        lock_t0[name] = t
                        EV.push(t, f"[{t:10.3f}] LOCK ON {name} (base=0x{base:08X})")
                    else:
                        # lock turned OFF -> measure duration
                        t0 = lock_t0.get(name)
                        if t0 is not None:
                            dur_s = t - t0
                            frames = int(round(dur_s * FPS))
                            EV.push(t, f"[{t:10.3f}] LOCK OFF {name}  dur={dur_s:.3f}s (~{frames}f)")
                        lock_t0[name] = None

            # HUD render + lingering events
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

                extras = []
                # pooled-life quick view
                for nm, _ in SLOTS:
                    cur = pool_now.get(nm); prv = pool_prev.get(nm)
                    if cur is None:
                        extras.append(f"{nm} pool(+0x{POOL_REL:03X}): --")
                    else:
                        delta = (0 if prv is None else (cur - prv))
                        color = Colors.GREEN if delta > 0 else (Colors.RED if delta < 0 else "")
                        reset = Colors.RESET if color else ""
                        extras.append(f"{nm} pool(+0x{POOL_REL:03X}): {cur:>3}  {color}Δ{delta:+}{reset}")

                # activity/lock live view
                for nm, _ in SLOTS:
                    cur_bytes = wb_now.get(nm, {})
                    a = cur_bytes.get(LOCK_BYTES[0], None)
                    b = cur_bytes.get(LOCK_BYTES[1], None)
                    locked = lock_now.get(nm)
                    # live duration while locked
                    dur_txt = ""
                    if locked:
                        t0 = lock_t0.get(nm)
                        if t0 is not None:
                            frames = int(round((t - t0) * FPS))
                            dur_txt = f"  t≈{frames}f"
                    state_col = Colors.RED if locked else Colors.GREEN
                    state_txt = f"{state_col}{'LOCK' if locked else 'FREE'}{Colors.RESET}"
                    extras.append(
                        f"{nm} lock({'+0x%03X/+0x%03X' % LOCK_BYTES}): "
                        f"{state_txt}  a={a if a is not None else '--'}[{_bits8(a)}]  "
                        f"b={b if b is not None else '--'}[{_bits8(b)}]{dur_txt}"
                    )

                # X/Z live view (raw + Δ from baseline)
                for nm, _ in SLOTS:
                    xv = xw_now.get(nm); x0 = xw_base.get(nm)
                    zv = zw_now.get(nm); z0 = zw_base.get(nm)
                    if xv is None:
                        extras.append(f"{nm} X(+0x{XWORD_REL:03X}): --")
                    else:
                        dx = (xv - x0) if x0 is not None else 0
                        extras.append(f"{nm} X(+0x{XWORD_REL:03X}): {xv:5d}  Δ{dx:+}")
                    if zv is None:
                        extras.append(f"{nm} Z?(+0x{ZWORD_REL:03X}): --")
                    else:
                        dz = (zv - z0) if z0 is not None else 0
                        extras.append(f"{nm} Z?(+0x{ZWORD_REL:03X}): {zv:5d}  Δ{dz:+}")

                # lingering scan/event lines
                extras.extend(EV.render_lines(t))

                render_screen(lines, meter_summary, extras)

            time.sleep(INTERVAL)

    except KeyboardInterrupt:
        pass
    finally:
        if SHOW_HUD:
            sys.stdout.write("\033[?25h"); sys.stdout.flush()
        try:
            if csv_fh: csv_fh.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()