# tvc_move_viewer.py
#
# GUI viewer for TvC move tables
# features:
# - auto-detect 4 loaded character tables (by tail clusters)
# - per-slot move listing (P1-C1, P2-C1, etc. via remap)
# - for each move:
#     * anim id → name
#     * meter block
#     * animation speed
#     * active frames
#     * attack damage
#     * attack property
#     * hit reaction
#     * knockback distance
#     * stuns (hitstun / blockstun / hitstop)
#
# requires: dolphin_io.py, constants.py

import sys
import tkinter as tk
from tkinter import ttk

print("[viewer] starting…")

try:
    from dolphin_io import hook, rbytes, rd32
    from constants import MEM2_LO, MEM2_HI, SLOTS, CHAR_NAMES
    print("[viewer] dolphin modules imported")
except Exception as e:
    print("[viewer] FAILED to import dolphin modules:", e)
    sys.exit(1)

# ------------------------------------------------------------
# patterns / tables
# ------------------------------------------------------------
TAIL_PATTERN = b"\x00\x00\x00\x38\x01\x33\x00\x00"
CLUSTER_GAP = 0x4000

# base animation header
ANIM_HDR = [
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x01, 0xE8,
    0x3F, 0x00, 0x00, 0x00,
]

# "button can be altered w/ directional input"
CMD_HDR = [
    0x04, 0x03, 0x60, 0x00, 0x00, 0x00, 0x13, 0xCC,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x08,
    0x01, 0x34, 0x00, 0x00, 0x00,
]
CMD_HDR_LEN = len(CMD_HDR)

# air variant block
AIR_HDR = [
    0x33, 0x33, 0x20, 0x00, 0x01, 0x34, 0x00, 0x00, 0x00,
]
AIR_HDR_LEN = len(AIR_HDR)

# end-of-attack / super-ish header
SUPER_END_HDR = [
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x12, 0x18, 0x3F,
]

# inside the header, later, we look for 01 ?? 01 3C
ANIM_ID_PATTERN = [0x01, None, 0x01, 0x3C]
LOOKAHEAD_AFTER_HDR = 0x80

# meter gain block
METER_HDR = [
    0x34, 0x04, 0x00, 0x20, 0x00, 0x00, 0x00, 0x03,
    0x00, 0x00, 0x00, 0x00,
    0x36, 0x43, 0x00, 0x20, 0x00, 0x00, 0x00,
    0x36, 0x43, 0x00, 0x20, 0x00, 0x00, 0x00,
]
METER_TOTAL_LEN = len(METER_HDR) + 5
METER_PAIR_RANGE = 0x400

# animation speed block
ANIM_SPEED_HDR = [
    0x04, 0x01, 0x02, 0x3F, 0x00, 0x00, 0x01,
]

# active frames block
ACTIVE_HDR = [
    0x20, 0x35, 0x01, 0x20, 0x3F, 0x00, 0x00, 0x00,
]
ACTIVE_TOTAL_LEN = 20
ACTIVE_PAIR_RANGE = 0x400

# attack damage block
DAMAGE_HDR = [
    0x35, 0x10, 0x20, 0x3F, 0x00,
]
DAMAGE_TOTAL_LEN = 16
DAMAGE_PAIR_RANGE = 0x400

# attack property block
ATKPROP_HDR = [
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
]
ATKPROP_TOTAL_LEN = 17
ATKPROP_PAIR_RANGE = 0x400

# hit reaction block
HITREACTION_HDR = [
    0x04, 0x17, 0x60, 0x00, 0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00, 0x80, 0x04, 0x2F, 0x00,
    0x04, 0x15, 0x60, 0x00, 0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00,
]
HITREACTION_TOTAL_LEN = 33
HITREACTION_PAIR_RANGE = 0x400

# knockback distance
KNOCKBACK_HDR = [
    0x35, None, None, 0x20, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00,
]
KNOCKBACK_TOTAL_LEN = 20
KNOCKBACK_PAIR_RANGE = 0x400

KNOCKBACK_TRAJ_MEANING = {
    0xBD: "up-fwd KB",
    0xBE: "down-fwd KB",
    0xBC: "up KB",
    0xC4: "pop",
}

# stun block
# 04 01 60 ... 02 54 ... XX
# 04 01 60 ... 02 58 ... YY
# 33 32 00 20 00 00 00 ZZ 04 15 60
STUN_HDR = [
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x54,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, None,
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x58,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, None,
    0x33, 0x32, 0x00, 0x20, 0x00, 0x00, 0x00, None,
    0x04, 0x15, 0x60,
]
STUN_TOTAL_LEN = 43
STUN_PAIR_RANGE = 0x400

# meaning tables
ATKPROP_MEANING = {
    0x04: "Unblockable",
    0x09: "Mid L",
    0x0A: "Mid M",
    0x0C: "Mid H",
    0x11: "High L",
    0x12: "High M",
    0x14: "High H",
    0x21: "Low L",
    0x22: "Low M",
    0x24: "Low H",
}

HITREACTION_MEANING = {
    0x000000: "Stay on ground",
    0x000001: "KB",
    0x000002: "KD",
    0x000003: "Spiral KD",
    0x000004: "Sweep",
    0x000008: "Stagger",
    0x000010: "G stay, A KB",
    0x000040: "G stay, A KB, OTG stay",
    0x000041: "KB, OTG stay",
    0x000042: "KD, OTG stay",
    0x000080: "G stay, A KB",
    0x000082: "KD",
    0x000083: "Spiral KD",
    0x000400: "Launcher",
    0x000800: "G stay, A soft KD",
    0x000848: "Stagger / soft KD",
    0x002010: "G stay, A KB",
    0x003010: "G stay, A KB",
    0x004200: "KD",
    0x800000: "Crumple, A KB",
    0x800002: "KD, wallbounce",
    0x800008: "Flash Chop",
    0x800020: "Snapback",
    0x800082: "KD, wallbounce",
    0x001001: "Weird grab",
    0x001003: "Weird grab 2",
}

# Ryu-based animation names
ANIM_MAP = {
    0x00: "5A",
    0x01: "5B",
    0x02: "5C",
    0x03: "2A",
    0x04: "2B",
    0x05: "2C",
    0x06: "6C",
    0x08: "3C",
    0x09: "j.A",
    0x0A: "j.B",
    0x0B: "j.C",
    0x0E: "6B",
    0x14: "donkey/dash-ish",

    # everything below here is from your poke list — we’re inventing IDs
    0x15: "Tatsu L",
    0x16: "Tatsu M (first hit)",
    0x17: "Tatsu M (second/third hit)",
    0x18: "Tatsu H (first hit)",
    0x19: "Tatsu H (last three hits)",

    0x1A: "Tatsu L (air)",
    0x1B: "Tatsu L (air, second hit)",
    0x1C: "Tatsu M (air, first hit)",
    0x1D: "Tatsu M (air, second/third hit)",
    0x1E: "Tatsu H (air, first)",
    0x1F: "Tatsu H (air, rest)",

    0x20: "Shoryu L",
    0x21: "Shoryu M (second hit)",
    0x22: "Shoryu M (first hit)",
    0x23: "Shoryu H (first hit)",
    0x24: "Shoryu H (second hit)",

    0x25: "Donkey L",
    0x26: "Donkey M",
    0x27: "Donkey H",

    0x28: "Tatsu Super",

    # your extra ones:
    0x29: "ShinSho (hit 1)",
    0x2A: "ShinSho (hit 2)",
    0x2B: "ShinSho (hit 3/4?)",
    0x2C: "ShinSho (last?)",
}

NORMAL_IDS = set(ANIM_MAP.keys())

# default meter per normal
DEFAULT_METER = {
    0x00: 0x32,  # light
    0x03: 0x32,
    0x09: 0x32,
    0x01: 0x64,  # medium
    0x04: 0x64,
    0x0A: 0x64,
    0x02: 0x96,  # heavy
    0x05: 0x96,
    0x0B: 0x96,
    0x06: 0x96,
    0x08: 0x96,
    0x0E: 0x96,
    0x14: 0x96,
}
SPECIAL_DEFAULT_METER = 0xC8  # special/super-ish

METER_LABELS = {
    0x32: "L",
    0x64: "M",
    0x96: "H",
    0xC8: "S",
}


# ------------------------------------------------------------
# helpers
# ------------------------------------------------------------
def match_bytes(buf, pos, pat):
    L = len(pat)
    if pos < 0 or pos + L > len(buf):
        return False
    for i, b in enumerate(pat):
        if b is None:
            continue
        if buf[pos + i] != b:
            return False
    return True


def get_anim_id_after_hdr(buf, hdr_pos):
    start = hdr_pos + len(ANIM_HDR)
    end = min(start + LOOKAHEAD_AFTER_HDR, len(buf))
    for p in range(start, end - len(ANIM_ID_PATTERN) + 1):
        if match_bytes(buf, p, ANIM_ID_PATTERN):
            return buf[p + 1]
    return None


def find_all_tails(mem):
    offs = []
    off = 0
    while True:
        i = mem.find(TAIL_PATTERN, off)
        if i == -1:
            break
        offs.append(i)
        off = i + 1
    return offs


def cluster_tails(tail_offs):
    if not tail_offs:
        return []
    clusters = []
    cur = [tail_offs[0]]
    for o in tail_offs[1:]:
        if o - cur[-1] <= CLUSTER_GAP:
            cur.append(o)
        else:
            clusters.append(cur)
            cur = [o]
    clusters.append(cur)
    return clusters


def read_slots():
    out = []
    for slot_label, ptr_addr, teamtag in SLOTS:
        base = rd32(ptr_addr)
        cid = None
        cname = "—"
        if base:
            cid = rd32(base + 0x14)
            if cid is not None:
                cname = CHAR_NAMES.get(cid, f"ID_{cid}")
        out.append((slot_label, base, cid, cname))
    return out


def parse_anim_speed(buf: bytes, start: int):
    if start + 24 > len(buf):
        return None, None
    for i, b in enumerate(ANIM_SPEED_HDR):
        if buf[start + i] != b:
            return None, None
    anim_id = buf[start + len(ANIM_SPEED_HDR)]
    if buf[start + len(ANIM_SPEED_HDR) + 1] != 0x01 or buf[start + len(ANIM_SPEED_HDR) + 2] != 0x3C:
        return None, None
    tail_pattern = [0x33, 0x35, 0x20, 0x3F, 0x00, 0x00, 0x00]
    search_from = start + 16
    search_to = min(start + 64, len(buf))
    for p in range(search_from, search_to - len(tail_pattern) + 1):
        if match_bytes(buf, p, tail_pattern):
            speed_pos = p + len(tail_pattern)
            if speed_pos < len(buf):
                speed_val = buf[speed_pos]
                return anim_id, speed_val
    return None, None


def parse_active_frames(buf: bytes, start: int):
    if start + 20 > len(buf):
        return None, None
    for i, b in enumerate(ACTIVE_HDR):
        if buf[start + i] != b:
            return None, None
    xx = buf[start + 8]
    yy = buf[start + 16]
    return (xx + 1, yy + 1)


def parse_damage(buf: bytes, start: int):
    if start + 16 > len(buf):
        return None, None
    for i, b in enumerate(DAMAGE_HDR):
        if buf[start + i] != b:
            return None, None
    d0 = buf[start + 5]
    d1 = buf[start + 6]
    d2 = buf[start + 7]
    damage_val = (d0 << 16) | (d1 << 8) | d2
    dmg_flag = buf[start + 15]
    return damage_val, dmg_flag


def parse_atkprop(buf: bytes, start: int):
    if start + 17 > len(buf):
        return None
    for i, b in enumerate(ATKPROP_HDR):
        if buf[start + i] != b:
            return None
    prop = buf[start + len(ATKPROP_HDR)]
    return prop


def parse_hitreaction(buf: bytes, start: int):
    if start + HITREACTION_TOTAL_LEN > len(buf):
        return None
    for i, b in enumerate(HITREACTION_HDR):
        if buf[start + i] != b:
            return None
    xx = buf[start + 28]
    yy = buf[start + 29]
    zz = buf[start + 30]
    code = (xx << 16) | (yy << 8) | zz
    return code


def parse_knockback(buf: bytes, start: int):
    if start + KNOCKBACK_TOTAL_LEN > len(buf):
        return None
    kb0 = buf[start + 1]
    kb1 = buf[start + 2]
    traj = buf[start + 12]
    return kb0, kb1, traj


def parse_stun(buf: bytes, start: int):
    # we already matched STUN_HDR
    if start + STUN_TOTAL_LEN > len(buf):
        return None
    hitstun = buf[start + 15]
    blockstun = buf[start + 31]
    hitstop = buf[start + 38]
    return hitstun, blockstun, hitstop


# ------------------------------------------------------------
def scan_once():
    slots_info = read_slots()
    mem = rbytes(MEM2_LO, MEM2_HI - MEM2_LO)
    tails = find_all_tails(mem)
    tails.sort()
    clusters = cluster_tails(tails)

    # empiric remap
    cluster_to_slot = [0, 2, 1, 3]

    result = []
    max_chars = min(4, len(clusters))
    for _ in range(max_chars):
        result.append({
            "slot_label": "",
            "char_name": "",
            "moves": [],
        })

    for c_idx in range(max_chars):
        tails_in_cluster = clusters[c_idx]
        start_off = tails_in_cluster[0]
        if c_idx + 1 < len(clusters):
            end_off = clusters[c_idx + 1][0]
        else:
            end_off = min(len(mem), start_off + 0x8000)

        buf = mem[start_off:end_off]
        base_abs = MEM2_LO + start_off

        # PASS 1: moves
        moves = []
        i = 0
        while i < len(buf):
            if match_bytes(buf, i, SUPER_END_HDR):
                moves.append({"kind": "super", "abs": base_abs + i, "id": None})
                i += len(SUPER_END_HDR)
                continue

            if match_bytes(buf, i, AIR_HDR):
                s0 = i + AIR_HDR_LEN
                s1 = min(s0 + LOOKAHEAD_AFTER_HDR, len(buf))
                for p in range(s0, s1):
                    if match_bytes(buf, p, ANIM_HDR):
                        aid = get_anim_id_after_hdr(buf, p)
                        kind = "normal" if (aid is not None and aid in NORMAL_IDS) else "special"
                        moves.append({"kind": kind, "abs": base_abs + p, "id": aid})
                        break
                i += AIR_HDR_LEN
                continue

            if match_bytes(buf, i, CMD_HDR):
                s0 = i + CMD_HDR_LEN + 3
                s1 = min(s0 + LOOKAHEAD_AFTER_HDR, len(buf))
                for p in range(s0, s1):
                    if match_bytes(buf, p, ANIM_HDR):
                        aid = get_anim_id_after_hdr(buf, p)
                        kind = "normal" if (aid is not None and aid in NORMAL_IDS) else "special"
                        moves.append({"kind": kind, "abs": base_abs + p, "id": aid})
                        break
                i += CMD_HDR_LEN
                continue

            if match_bytes(buf, i, ANIM_HDR):
                aid = get_anim_id_after_hdr(buf, i)
                kind = "normal" if (aid is not None and aid in NORMAL_IDS) else "special"
                moves.append({"kind": kind, "abs": base_abs + i, "id": aid})
                i += len(ANIM_HDR)
                continue

            i += 1

        # PASS 2: meter
        meters = []
        j = 0
        while j < len(buf):
            if match_bytes(buf, j, METER_HDR):
                if j + METER_TOTAL_LEN <= len(buf):
                    meter_val = buf[j + len(METER_HDR)]
                    meters.append((base_abs + j, meter_val))
                j += len(METER_HDR)
                continue
            j += 1

        # PASS 3: animation speed
        speed_map = {}
        k = 0
        while k < len(buf):
            aid, spd = parse_anim_speed(buf, k)
            if aid is not None:
                speed_map[aid] = spd
                k += 24
                continue
            k += 1

        # PASS 4: active frames
        active_blocks = []
        q = 0
        while q < len(buf):
            if match_bytes(buf, q, ACTIVE_HDR):
                a_start, a_end = parse_active_frames(buf, q)
                if a_start is not None:
                    active_blocks.append((base_abs + q, a_start, a_end))
                q += ACTIVE_TOTAL_LEN
                continue
            q += 1

        # PASS 5: damage
        dmg_blocks = []
        d = 0
        while d < len(buf):
            if match_bytes(buf, d, DAMAGE_HDR):
                dmg_val, dmg_flag = parse_damage(buf, d)
                if dmg_val is not None:
                    dmg_blocks.append((base_abs + d, dmg_val, dmg_flag))
                d += DAMAGE_TOTAL_LEN
                continue
            d += 1

        # PASS 6: attack property
        atkprop_blocks = []
        ap = 0
        while ap < len(buf):
            if match_bytes(buf, ap, ATKPROP_HDR):
                prop = parse_atkprop(buf, ap)
                if prop is not None:
                    atkprop_blocks.append((base_abs + ap, prop))
                ap += ATKPROP_TOTAL_LEN
                continue
            ap += 1

        # PASS 7: hit reaction
        hitreact_blocks = []
        hr = 0
        while hr < len(buf):
            if match_bytes(buf, hr, HITREACTION_HDR):
                code = parse_hitreaction(buf, hr)
                if code is not None:
                    hitreact_blocks.append((base_abs + hr, code))
                hr += HITREACTION_TOTAL_LEN
                continue
            hr += 1

        # PASS 8: knockback
        kb_blocks = []
        kbpos = 0
        while kbpos < len(buf):
            if match_bytes(buf, kbpos, KNOCKBACK_HDR):
                kb_data = parse_knockback(buf, kbpos)
                if kb_data is not None:
                    kb_blocks.append((base_abs + kbpos, kb_data))
                kbpos += KNOCKBACK_TOTAL_LEN
                continue
            kbpos += 1

        # PASS 9: stuns
        stun_blocks = []
        sb = 0
        while sb < len(buf):
            if match_bytes(buf, sb, STUN_HDR):
                stun_data = parse_stun(buf, sb)
                if stun_data is not None:
                    stun_blocks.append((base_abs + sb, stun_data))
                sb += STUN_TOTAL_LEN
                continue
            sb += 1

        # PASS 10: attach to moves
        for mv in moves:
            aid = mv["id"]
            mv_abs = mv["abs"]

            # meter
            if mv["kind"] == "normal":
                mv["meter"] = DEFAULT_METER.get(aid)
            elif mv["kind"] == "special":
                mv["meter"] = SPECIAL_DEFAULT_METER
            else:
                mv["meter"] = None
            best_val = mv["meter"]
            best_dist = None
            for m_abs, m_val in meters:
                if m_abs >= mv_abs and m_abs - mv_abs <= METER_PAIR_RANGE:
                    dist = m_abs - mv_abs
                    if best_dist is None or dist < best_dist:
                        best_dist = dist
                        best_val = m_val
            mv["meter"] = best_val

            # speed
            if aid is not None and aid in speed_map:
                mv["speed"] = speed_map[aid]
            else:
                mv["speed"] = None

            # active
            mv_active_start = None
            mv_active_end = None
            best_dist = None
            for a_abs, a_start, a_end in active_blocks:
                if a_abs >= mv_abs and a_abs - mv_abs <= ACTIVE_PAIR_RANGE:
                    dist = a_abs - mv_abs
                    if best_dist is None or dist < best_dist:
                        best_dist = dist
                        mv_active_start = a_start
                        mv_active_end = a_end
            mv["active_start"] = mv_active_start
            mv["active_end"] = mv_active_end

            # damage
            mv_dmg = None
            mv_dflag = None
            best_dist = None
            for dmg_abs, dmg_val, dmg_flag in dmg_blocks:
                if dmg_abs >= mv_abs and dmg_abs - mv_abs <= DAMAGE_PAIR_RANGE:
                    dist = dmg_abs - mv_abs
                    if best_dist is None or dist < best_dist:
                        best_dist = dist
                        mv_dmg = dmg_val
                        mv_dflag = dmg_flag
            mv["damage"] = mv_dmg
            mv["damage_flag"] = mv_dflag

            # attack property
            mv_prop = None
            best_dist = None
            for ap_abs, prop in atkprop_blocks:
                if ap_abs >= mv_abs and ap_abs - mv_abs <= ATKPROP_PAIR_RANGE:
                    dist = ap_abs - mv_abs
                    if best_dist is None or dist < best_dist:
                        best_dist = dist
                        mv_prop = prop
            mv["attack_property"] = mv_prop

            # hit reaction
            mv_react = None
            best_dist = None
            for hr_abs, code in hitreact_blocks:
                if hr_abs >= mv_abs and hr_abs - mv_abs <= HITREACTION_PAIR_RANGE:
                    dist = hr_abs - mv_abs
                    if best_dist is None or dist < best_dist:
                        best_dist = dist
                        mv_react = code
            mv["hit_reaction"] = mv_react

            # knockback
            mv_kb0 = None
            mv_kb1 = None
            mv_kb_traj = None
            best_dist = None
            for kb_abs, (kb0, kb1, traj) in kb_blocks:
                if kb_abs >= mv_abs and kb_abs - mv_abs <= KNOCKBACK_PAIR_RANGE:
                    dist = kb_abs - mv_abs
                    if best_dist is None or dist < best_dist:
                        best_dist = dist
                        mv_kb0 = kb0
                        mv_kb1 = kb1
                        mv_kb_traj = traj
            mv["kb0"] = mv_kb0
            mv["kb1"] = mv_kb1
            mv["kb_traj"] = mv_kb_traj

            # stuns
            mv_hitstun = None
            mv_blockstun = None
            mv_hitstop = None
            best_dist = None
            for stun_abs, (hs, bs, hsop) in stun_blocks:
                if stun_abs >= mv_abs and stun_abs - mv_abs <= STUN_PAIR_RANGE:
                    dist = stun_abs - mv_abs
                    if best_dist is None or dist < best_dist:
                        best_dist = dist
                        mv_hitstun = hs
                        mv_blockstun = bs
                        mv_hitstop = hsop
            mv["hitstun"] = mv_hitstun
            mv["blockstun"] = mv_blockstun
            mv["hitstop"] = mv_hitstop

        # assign to actual slot
        slot_idx = cluster_to_slot[c_idx] if c_idx < len(cluster_to_slot) else c_idx
        slot_label, base_ptr, cid, cname = (
            slots_info[slot_idx] if slot_idx < len(slots_info) else (f"slot{slot_idx}", 0, None, "—")
        )
        result[slot_idx] = {
            "slot_label": slot_label,
            "char_name": cname,
            "moves": moves,
        }

    return result


# ------------------------------------------------------------
# GUI
# ------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TvC move viewer")
        self.geometry("1800x600")

        left = ttk.Frame(self); left.pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=4)
        ttk.Label(left, text="Characters").pack(anchor="w")

        self.slot_list = tk.Listbox(left, height=6)
        self.slot_list.pack(fill=tk.Y, expand=False)
        self.slot_list.bind("<<ListboxSelect>>", self.on_slot_sel)

        self.refresh_btn = ttk.Button(left, text="Refresh", command=self.refresh_data)
        self.refresh_btn.pack(pady=6)

        right = ttk.Frame(self); right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=4, pady=4)

        cols = (
            "move", "addr", "meter", "speed", "active",
            "damage", "dflag", "atkprop", "hitreact",
            "knockback", "stun"
        )
        self.tree = ttk.Treeview(right, columns=cols, show="headings")
        self.tree.heading("move", text="Move")
        self.tree.heading("addr", text="Addr")
        self.tree.heading("meter", text="Meter")
        self.tree.heading("speed", text="Speed")
        self.tree.heading("active", text="Active")
        self.tree.heading("damage", text="Damage")
        self.tree.heading("dflag", text="DmgFlag")
        self.tree.heading("atkprop", text="AtkProp")
        self.tree.heading("hitreact", text="HitReact")
        self.tree.heading("knockback", text="KB")
        self.tree.heading("stun", text="Stun")

        self.tree.column("move", width=200)
        self.tree.column("addr", width=120)
        self.tree.column("meter", width=80)
        self.tree.column("speed", width=60)
        self.tree.column("active", width=100)
        self.tree.column("damage", width=90)
        self.tree.column("dflag", width=70)
        self.tree.column("atkprop", width=110)
        self.tree.column("hitreact", width=150)
        self.tree.column("knockback", width=140)
        self.tree.column("stun", width=140)

        self.tree.pack(fill=tk.BOTH, expand=True)

        self.slots_data = []
        self.refresh_data()

    def refresh_data(self):
        try:
            self.slots_data = scan_once()
        except Exception as e:
            print("[viewer] scan FAILED:", e)
            return

        self.slot_list.delete(0, tk.END)
        for idx, sd in enumerate(self.slots_data):
            label = sd.get("slot_label", f"slot{idx}")
            cname = sd.get("char_name", "—")
            self.slot_list.insert(tk.END, f"{label} ({cname})")

        if self.slots_data:
            self.slot_list.selection_clear(0, tk.END)
            self.slot_list.selection_set(0)
            self.slot_list.event_generate("<<ListboxSelect>>")

    def on_slot_sel(self, event):
        sel = self.slot_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self.slots_data):
            return

        slot = self.slots_data[idx]
        moves = slot.get("moves", [])

        # clear table
        for iid in self.tree.get_children():
            self.tree.delete(iid)

        # sort by anim id and address
        moves_sorted = sorted(
            moves,
            key=lambda m: ((m["id"] if m["id"] is not None else 0xFF), m["abs"])
        )

        for mv in moves_sorted:
            aid = mv.get("id")
            addr = mv.get("abs", 0)
            meter = mv.get("meter")
            speed = mv.get("speed")
            a_s = mv.get("active_start")
            a_e = mv.get("active_end")
            dmg = mv.get("damage")
            dflag = mv.get("damage_flag")
            atkprop = mv.get("attack_property")
            hitreact = mv.get("hit_reaction")
            kb0 = mv.get("kb0")
            kb1 = mv.get("kb1")
            kb_traj = mv.get("kb_traj")
            hs = mv.get("hitstun")
            bs = mv.get("blockstun")
            hstop = mv.get("hitstop")

            # ---- Move name ----
            if aid is not None:
                name = ANIM_MAP.get(aid, f"anim_{aid:02X}")
            else:
                name = "SUPER?" if mv.get("kind") == "super" else "SPECIAL?"

            addr_txt = f"0x{addr:08X}"

            # ---- Meter ----
            if meter is not None:
                label = METER_LABELS.get(meter)
                if label:
                    meter_txt = f"{meter:02X} ({label})"
                else:
                    meter_txt = f"{meter:02X}"
            else:
                meter_txt = ""

            # ---- Speed ----
            speed_txt = f"{speed:02X}" if speed is not None else ""

            # ---- Active Frames ----
            if a_s is not None and a_e is not None:
                active_txt = f"{a_s}-{a_e}"
            else:
                active_txt = ""

            # ---- Damage ----
            if dmg is not None:
                dmg_txt = f"{dmg:,d}"
            else:
                dmg_txt = ""

            dflag_txt = f"{dflag:02X}" if dflag is not None else ""

            # ---- Attack Property ----
            if atkprop is not None:
                short = ATKPROP_MEANING.get(atkprop)
                atkprop_txt = f"{atkprop:02X}" + (f" ({short})" if short else "")
            else:
                atkprop_txt = ""

            # ---- Hit Reaction ----
            if hitreact is not None:
                b0 = (hitreact >> 16) & 0xFF
                b1 = (hitreact >> 8) & 0xFF
                b2 = hitreact & 0xFF
                hr_name = HITREACTION_MEANING.get(hitreact)
                if hr_name:
                    hitreact_txt = f"{b0:02X} {b1:02X} {b2:02X} ({hr_name})"
                else:
                    hitreact_txt = f"{b0:02X} {b1:02X} {b2:02X}"
            else:
                hitreact_txt = ""

            # ---- Knockback ----
            if kb0 is not None and kb1 is not None:
                kb_txt = f"{kb0:02X} {kb1:02X}"
            else:
                kb_txt = ""
            if kb_traj is not None:
                traj_name = KNOCKBACK_TRAJ_MEANING.get(kb_traj)
                if traj_name:
                    kb_txt = (kb_txt + f" {kb_traj:02X} ({traj_name})").strip()
                else:
                    kb_txt = (kb_txt + f" {kb_traj:02X}").strip()

            # ---- Stuns → cleaner display ----
            def stun_to_text(label, val):
                if val is None:
                    return None
                # default to numeric conversion
                frame_guess = val
                # adjust for known measured values
                if val == 0x0C:
                    frame_guess = 10
                elif val == 0x0F:
                    frame_guess = 15
                elif val == 0x11:
                    frame_guess = 17
                elif val == 0x15:
                    frame_guess = 21
                return f"{label}:{frame_guess}f"

            stun_parts = []
            if hs is not None:
                stun_parts.append(stun_to_text("HS", hs))
            if bs is not None:
                stun_parts.append(stun_to_text("BS", bs))
            if hstop is not None:
                stun_parts.append(f"Stop:{hstop}")

            stun_txt = " ".join(p for p in stun_parts if p)

            # ---- Insert into Treeview ----
            self.tree.insert(
                "",
                tk.END,
                values=(
                    name,
                    addr_txt,
                    meter_txt,
                    speed_txt,
                    active_txt,
                    dmg_txt,
                    dflag_txt,
                    atkprop_txt,
                    hitreact_txt,
                    kb_txt,
                    stun_txt,
                ),
            )

if __name__ == "__main__":
    print("[viewer] hooking dolphin…")
    try:
        hook()
    except Exception as e:
        print("[viewer] hook FAILED:", e)
        sys.exit(1)
    print("[viewer] hook OK, launching GUI")
    app = App()
    app.mainloop()
