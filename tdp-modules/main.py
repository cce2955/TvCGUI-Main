# main.py
# Modular wiring for TvC HUD + HIT/COMBO logger + Hit-signature miner.

from __future__ import annotations
import os, csv, time, sys
from typing import Dict, Optional, Tuple, List

# --- Local modules ---
from config import (
    SHOW_HUD, HUD_REFRESH_HZ, INTERVAL, POLL_HZ,
    COMBO_TIMEOUT, MIN_HIT_DAMAGE, MAX_DIST2, METER_DELTA_MIN,
    HIT_CSV, COMBO_CSV, HIT_SIG_EVENT_CSV, HIT_SIG_SUMMARY_CSV, HIT_SIG_TOPN,
)
from constants import SLOTS
from colors import Colors  # for startup text + HUD show/hide cursor
from dolphin_io import hook
from resolver import SlotResolver, pick_posy_off_no_jump
from meter import read_meter, drop_meter_cache_for_base
from models import Fighter, read_fighter, dist2, ComboState
from hud import fmt_line, render_screen, log_event
from hit_signature import HitSignatureTracker
from attack_ids import read_attack_ids


def _fighter_to_blk(f: Optional[Fighter]) -> Optional[dict]:
    """Convert Fighter dataclass to the dict format expected by hud.fmt_line()."""
    if f is None:
        return None
    return {
        "base": f.base,
        "max": f.max,
        "cur": f.cur,
        "aux": f.aux,
        "id": f.id,
        "name": f.name,
        "x": f.x,
        "y": f.y,
        "last": f.last,
    }


def main():
    print(Colors.BOLD + "HUD + HIT logger + Hit-signature miner: waiting for Dolphin…" + Colors.RESET)
    hook()
    print("Hooked.")

    # Per-run logs folder
    LOGDIR = os.path.join("logs", time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(LOGDIR, exist_ok=True)

    # CSV writers (opened in LOGDIR)
    hit_path   = os.path.join(LOGDIR, HIT_CSV)
    combo_path = os.path.join(LOGDIR, COMBO_CSV)
    hs_evt_path = os.path.join(LOGDIR, HIT_SIG_EVENT_CSV)
    hs_sum_path = os.path.join(LOGDIR, HIT_SIG_SUMMARY_CSV)

    hit_new = not os.path.exists(hit_path)
    fh = open(hit_path, "a", newline=""); hitw = csv.writer(fh)
    if hit_new:
        hitw.writerow([
            "t","victim_label","victim_char","dmg","hp_before","hp_after",
            "team_guess","attacker_label","attacker_char",
            "attacker_id_dec","attacker_id_hex","attacker_move",
            "dist2","victim_ptr_hex"
        ])

    combo_new = not os.path.exists(combo_path)
    fc = open(combo_path, "a", newline=""); combow = csv.writer(fc)
    if combo_new:
        combow.writerow([
            "t_start","t_end","dur","victim_label","victim_char",
            "attacker_label","attacker_char","attacker_move","team_guess",
            "hits","total","hp_start","hp_end"
        ])

    hs_evt_new = not os.path.exists(hs_evt_path)
    hs_fh = open(hs_evt_path, "a", newline=""); hs_w = csv.writer(hs_fh)
    if hs_evt_new:
        hs_w.writerow(["t","label","slot","base_hex","rel_off_hex","stride","pre","hit","post"])

    # summary CSV is always re-written from scratch
    hss_fh = open(hs_sum_path, "w", newline=""); hss_w = csv.writer(hss_fh)
    hss_w.writerow(["rel_off_hex","stride","hit_flips","nonhit_flips","lift"])

    # Construct miner
    HITSIG = HitSignatureTracker(event_writer=hs_w, summary_writer=hss_w)

    # Runtime state
    resolver = SlotResolver()
    last_base_by_slot: Dict[int, int] = {}
    y_off_by_base: Dict[int, int] = {}
    meter_prev = {"P1": 0, "P2": 0}
    meter_now  = {"P1": 0, "P2": 0}
    p1_c1_base: Optional[int] = None
    p2_c1_base: Optional[int] = None

    prev_hp: Dict[int, int] = {}
    prev_last: Dict[int, Optional[int]] = {}
    combos: Dict[int, ComboState] = {}

    # Static HUD init
    if SHOW_HUD:
        sys.stdout.write("\033[?25l")  # hide cursor
        sys.stdout.flush()
    _last_hud = 0.0
    HUD_REFRESH_INTERVAL = 1.0 / HUD_REFRESH_HZ

    try:
        while True:
            # Resolve bases for each slot
            resolved: List[Tuple[str, int, Optional[int], bool]] = []
            for name, slot in SLOTS:
                base, changed = resolver.resolve_base(slot)
                resolved.append((name, slot, base, changed))

            # Handle base changes & Y-offset selection
            for name, slot, base, changed in resolved:
                if base and last_base_by_slot.get(slot) != base:
                    last_base_by_slot[slot] = base
                    drop_meter_cache_for_base(base)
                    y_off_by_base[base] = pick_posy_off_no_jump(base)

            # Identify team leaders' bases
            for name, slot, base, _ in resolved:
                if name == "P1-C1" and base:
                    p1_c1_base = base
                if name == "P2-C1" and base:
                    p2_c1_base = base

            # Meter reads
            meter_prev["P1"] = meter_now["P1"]
            meter_prev["P2"] = meter_now["P2"]
            m1 = read_meter(p1_c1_base)
            m2 = read_meter(p2_c1_base)
            if m1 is not None: meter_now["P1"] = m1
            if m2 is not None: meter_now["P2"] = m2

            # Fighter snapshots + signature snapshots
            info: Dict[str, Optional[Fighter]] = {}
            for name, slot, base, _ in resolved:
                if base:
                    y = y_off_by_base.get(base, 0xF4)
                    info[name] = read_fighter(base, y)
                    # snapshot for hit miner (use slot label & base)
                    HITSIG.snapshot_now(name, base)
                else:
                    info[name] = None

            t = time.time()

            # HIT detection
            for name, slot, base, _ in resolved:
                fi = info.get(name)
                if not fi or not base:
                    continue

                cur = fi.cur
                last = fi.last
                hp_prev = prev_hp.get(base)
                last_prev = prev_last.get(base)

                took = False
                dmg: Optional[int] = None

                # Primary method: change in victim's last-damage field (+0x40)
                if last is not None and last_prev is not None and last > 0 and last != last_prev:
                    took = True
                    dmg = last

                # Fallback: HP drop
                if (not took) and hp_prev is not None and cur is not None and cur < hp_prev:
                    took = True
                    dmg = hp_prev - cur

                if took and dmg and dmg >= MIN_HIT_DAMAGE:
                    # Team guess via meter delta
                    dP1 = (meter_now["P1"] or 0) - (meter_prev["P1"] or 0)
                    dP2 = (meter_now["P2"] or 0) - (meter_prev["P2"] or 0)
                    team_guess: Optional[str] = None
                    if abs(dP1 - dP2) >= METER_DELTA_MIN:
                        team_guess = "P1" if dP1 > dP2 else "P2"

                    # Nearest opponent as attacker guess
                    opp = ["P2-C1", "P2-C2"] if name.startswith("P1") else ["P1-C1", "P1-C2"]
                    cand = [info.get(k) for k in opp if info.get(k)]
                    best: Optional[Fighter] = None
                    best_label: Optional[str] = None
                    d2 = float("inf")
                    if cand:
                        best = min(cand, key=lambda o: dist2(fi, o))
                        d2 = dist2(fi, best)
                        if (MAX_DIST2 is not None) and (d2 > MAX_DIST2):
                            best = None
                            d2 = -1.0
                    if best:
                        for k, v in info.items():
                            if v is best:
                                best_label = k
                                break

                    # Attack IDs (if we guessed an attacker)
                    attacker_base = best.base if best else None
                    atk_id, atk_sub, atk_name = read_attack_ids(attacker_base) if attacker_base else (None, None, "")
                    hp_before = hp_prev if hp_prev is not None else (fi.cur + dmg)
                    hp_after = fi.cur

                    # Log hit row
                    hitw.writerow([
                        f"{t:.6f}", name, fi.name, int(dmg),
                        int(hp_before), int(hp_after),
                        (team_guess or ""),
                        (best_label or ""), (best.name if best else ""),
                        (int(atk_id) if atk_id is not None else ""),
                        (hex(int(atk_id)) if atk_id is not None else ""),
                        (atk_name or ""), f"{d2 if d2 != float('inf') else ''}",
                        f"0x{base:08X}"
                    ])
                    fh.flush()

                    # Event log line
                    log_event(
                        f"[{t:10.3f}] HIT  victim={name:<5}({fi.name:<12}) "
                        f"dmg={int(dmg):5d} hp:{int(hp_before)}->{int(hp_after)} "
                        f"team={team_guess or '--'} attacker≈{(best_label or '--'):<5}"
                        f"({(best.name if best else '--')}) d2={(d2 if d2 != float('inf') else -1):.3f} "
                        f"atk_id={(atk_id or '--')} atk_sub={(atk_sub or '--')}"
                    )

                    # Combo state
                    st = combos.get(base)
                    if st is None or not st.active:
                        st = ComboState()
                        st.begin(
                            t, base, name, fi.name, int(hp_before),
                            best_label, (best.name if best else None), "", team_guess
                        )
                        combos[base] = st
                    st.add_hit(t, int(dmg), int(hp_after))

                    # Feed hit to signature miner
                    HITSIG.on_true_hit(t, name, base)

                # update prevs
                if cur is not None:
                    prev_hp[base] = cur
                prev_last[base] = last

            # End expired combos
            for vb, st in list(combos.items()):
                if st.expired(t):
                    summary = st.end()
                    combow.writerow([
                        f"{summary['t0']:.6f}", f"{summary['t1']:.6f}", f"{summary['dur']:.6f}",
                        summary['victim_label'], summary['victim_name'],
                        (summary['attacker_label'] or ""), (summary['attacker_name'] or ""),
                        (summary.get('attacker_move') or ""), (summary['team_guess'] or ""),
                        summary['hits'], summary['total'],
                        summary['hp_start'], summary['hp_end']
                    ])
                    fc.flush()
                    log_event(
                        f"[{summary['t0']:10.3f}→{summary['t1']:10.3f}] COMBO "
                        f"{(summary['attacker_label'] or '--')}({(summary['attacker_name'] or '--')}) "
                        f"→ {summary['victim_label']}({summary['victim_name']})  "
                        f"hits={summary['hits']} total={summary['total']} "
                        f"hp:{summary['hp_start']}→{summary['hp_end']} team={summary['team_guess'] or '--'}"
                    )
                    del combos[vb]

            # Background non-hit sampling & periodic signature summary
            slots_with_bases = [(nm, info[nm].base) for nm, _ in SLOTS if info.get(nm)]
            HITSIG.background_nonhit_sample(t, slots_with_bases)
            # Every ~5 seconds
            if int(t * 10) % 50 == 0:
                HITSIG.write_summary_csv()

            # Static HUD render
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
                    f"P1:{meter_now['P1'] or 0:>6} (Δ{(meter_now['P1'] - meter_prev['P1']) or 0:+})  "
                    f"P2:{meter_now['P2'] or 0:>6} (Δ{(meter_now['P2'] - meter_prev['P2']) or 0:+})"
                )
                sig_lines = HITSIG.top_lines(HIT_SIG_TOPN)
                render_screen(lines, meter_summary, sig_lines)

            time.sleep(INTERVAL)

    except KeyboardInterrupt:
        pass
    finally:
        # Close CSVs
        for fh_ in (fh, fc, hs_fh, hss_fh):
            try:
                if fh_:
                    fh_.close()
            except Exception:
                pass
        # Show cursor again
        if SHOW_HUD:
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
