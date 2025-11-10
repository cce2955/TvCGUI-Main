# scan_normals_all.py
#
# Scans MEM2 for 4 TvC character move tables and extracts:
# - anim id
# - meter
# - speed
# - active frames
# - damage
# - attack property
# - hit reaction
# - knockback
# - stuns (hitstun, blockstun, hitstop)
#
# returns a list of up to 4 dicts:
# [
#   {"slot_label": "P1-C1", "char_name": "Ryu", "moves": [ {...}, ... ]},
#   ...
# ]
#
# used by main.py to show "scan normals (preview)" and full popout

from dolphin_io import hook, rbytes, rd32
from constants import MEM2_LO, MEM2_HI, SLOTS, CHAR_NAMES

# ------------------------------------------------------------
# constants and patterns
# ------------------------------------------------------------

# tail marker that ends a character block
TAIL_PATTERN = b"\x00\x00\x00\x38\x01\x33\x00\x00"
CLUSTER_GAP = 0x4000

# we back up a bit because 5A sometimes sits *before* the first tail
CLUSTER_PAD_BACK = 0x200  # 512 bytes

# base animation header
ANIM_HDR = [
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x01, 0xE8,
    0x3F, 0x00, 0x00, 0x00,
]

# "command" header (directional altered buttons)
CMD_HDR = [
    0x04, 0x03, 0x60, 0x00, 0x00, 0x00, 0x13, 0xCC,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x08,
    0x01, 0x34, 0x00, 0x00, 0x00,
]
CMD_HDR_LEN = len(CMD_HDR)

# air variant header
AIR_HDR = [
    0x33, 0x33, 0x20, 0x00, 0x01, 0x34, 0x00, 0x00, 0x00,
]
AIR_HDR_LEN = len(AIR_HDR)

# super-ish ending header
SUPER_END_HDR = [
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x12, 0x18, 0x3F,
]

# inside anim header we look for 01 ?? 01 3C
ANIM_ID_PATTERN = [0x01, None, 0x01, 0x3C]
LOOKAHEAD_AFTER_HDR = 0x80

# meter block
METER_HDR = [
    0x34, 0x04, 0x00, 0x20, 0x00, 0x00, 0x00, 0x03,
    0x00, 0x00, 0x00, 0x00,
    0x36, 0x43, 0x00, 0x20, 0x00, 0x00, 0x00,
    0x36, 0x43, 0x00, 0x20, 0x00, 0x00, 0x00,
]
METER_TOTAL_LEN = len(METER_HDR) + 5

# animation speed block
ANIM_SPEED_HDR = [
    0x04, 0x01, 0x02, 0x3F, 0x00, 0x00, 0x01,
]

# active frames block
ACTIVE_HDR = [
    0x20, 0x35, 0x01, 0x20, 0x3F, 0x00, 0x00, 0x00,
]
ACTIVE_TOTAL_LEN = 20

# damage block
DAMAGE_HDR = [
    0x35, 0x10, 0x20, 0x3F, 0x00,
]
DAMAGE_TOTAL_LEN = 16

# attack property block
ATKPROP_HDR = [
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
]
ATKPROP_TOTAL_LEN = 17

# hit reaction block
HITREACTION_HDR = [
    0x04, 0x17, 0x60, 0x00, 0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00, 0x80, 0x04, 0x2F, 0x00,
    0x04, 0x15, 0x60, 0x00, 0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00,
]
HITREACTION_TOTAL_LEN = 33

# knockback
KNOCKBACK_HDR = [
    0x35, None, None, 0x20, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00,
]
KNOCKBACK_TOTAL_LEN = 20

# stuns
STUN_HDR = [
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x54,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, None,
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x58,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, None,
    0x33, 0x32, 0x00, 0x20, 0x00, 0x00, 0x00, None,
    0x04, 0x15, 0x60,
]
STUN_TOTAL_LEN = 43

# we padded 0x200 earlier, so pair ranges need to be wider than the old 0x400
PAIR_RANGE = 0x600

METER_PAIR_RANGE = PAIR_RANGE
ACTIVE_PAIR_RANGE = PAIR_RANGE
DAMAGE_PAIR_RANGE = PAIR_RANGE
ATKPROP_PAIR_RANGE = PAIR_RANGE
HITREACTION_PAIR_RANGE = PAIR_RANGE
KNOCKBACK_PAIR_RANGE = PAIR_RANGE
STUN_PAIR_RANGE = PAIR_RANGE

# ------------------------------------------------------------
# name tables
# ------------------------------------------------------------

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

    # from your poke list — invented IDs so HUD can label them
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

    0x29: "ShinSho (hit 1)",
    0x2A: "ShinSho (hit 2)",
    0x2B: "ShinSho (hit 3/4?)",
    0x2C: "ShinSho (last?)",
}

NORMAL_IDS = set(ANIM_MAP.keys())

DEFAULT_METER = {
    0x00: 0x32,
    0x03: 0x32,
    0x09: 0x32,
    0x01: 0x64,
    0x04: 0x64,
    0x0A: 0x64,
    0x02: 0x96,
    0x05: 0x96,
    0x0B: 0x96,
    0x06: 0x96,
    0x08: 0x96,
    0x0E: 0x96,
    0x14: 0x96,
}
SPECIAL_DEFAULT_METER = 0xC8

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
    tail_offs = sorted(tail_offs)
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
    aid = buf[start + len(ANIM_SPEED_HDR)]
    # expect 01 3C right after
    if buf[start + len(ANIM_SPEED_HDR) + 1] != 0x01 or buf[start + len(ANIM_SPEED_HDR) + 2] != 0x3C:
        return None, None
    # look for tail to get speed byte
    tail_pat = [0x33, 0x35, 0x20, 0x3F, 0x00, 0x00, 0x00]
    search_from = start + 16
    search_to = min(start + 64, len(buf))
    for p in range(search_from, search_to - len(tail_pat) + 1):
        if match_bytes(buf, p, tail_pat):
            spd = buf[p + len(tail_pat)]
            return aid, spd
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
    dmg_val = (d0 << 16) | (d1 << 8) | d2
    dmg_flag = buf[start + 15]
    return dmg_val, dmg_flag


def parse_atkprop(buf: bytes, start: int):
    if start + 17 > len(buf):
        return None
    for i, b in enumerate(ATKPROP_HDR):
        if buf[start + i] != b:
            return None
    return buf[start + len(ATKPROP_HDR)]


def parse_hitreaction(buf: bytes, start: int):
    if start + HITREACTION_TOTAL_LEN > len(buf):
        return None
    for i, b in enumerate(HITREACTION_HDR):
        if buf[start + i] != b:
            return None
    xx = buf[start + 28]
    yy = buf[start + 29]
    zz = buf[start + 30]
    return (xx << 16) | (yy << 8) | zz


def parse_knockback(buf: bytes, start: int):
    if start + KNOCKBACK_TOTAL_LEN > len(buf):
        return None
    kb0 = buf[start + 1]
    kb1 = buf[start + 2]
    traj = buf[start + 12]
    return kb0, kb1, traj


def parse_stun(buf: bytes, start: int):
    if start + STUN_TOTAL_LEN > len(buf):
        return None
    hs = buf[start + 15]
    bs = buf[start + 31]
    hsop = buf[start + 38]
    return hs, bs, hsop


# ------------------------------------------------------------
def scan_once():
    # user can call this directly
    hook()  # safe if already hooked

    slots_info = read_slots()
    mem = rbytes(MEM2_LO, MEM2_HI - MEM2_LO)

    tails = find_all_tails(mem)
    clusters = cluster_tails(tails)

    # empiric remap from your original
    cluster_to_slot = [0, 2, 1, 3]

    result = []
    max_chars = min(4, len(clusters))
    for _ in range(max_chars):
        result.append({"slot_label": "", "char_name": "", "moves": []})

    for c_idx in range(max_chars):
        tails_in_cluster = clusters[c_idx]

        start_off = tails_in_cluster[0]
        start_off = max(0, start_off - CLUSTER_PAD_BACK)

        if c_idx + 1 < len(clusters):
            end_off = clusters[c_idx + 1][0]
        else:
            end_off = min(len(mem), start_off + 0x8000)

        buf = mem[start_off:end_off]
        base_abs = MEM2_LO + start_off

        # PASS 1: find move headers
        moves = []
        i = 0
        while i < len(buf):
            # super-ish
            if match_bytes(buf, i, SUPER_END_HDR):
                moves.append({"kind": "super", "abs": base_abs + i, "id": None})
                i += len(SUPER_END_HDR)
                continue

            # air
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

            # command
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

            # plain anim
            if match_bytes(buf, i, ANIM_HDR):
                aid = get_anim_id_after_hdr(buf, i)
                kind = "normal" if (aid is not None and aid in NORMAL_IDS) else "special"
                moves.append({"kind": kind, "abs": base_abs + i, "id": aid})
                i += len(ANIM_HDR)
                continue

            i += 1

        # PASS 2: meter blocks
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

        # PASS 3: anim speed
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
                a_s, a_e = parse_active_frames(buf, q)
                if a_s is not None:
                    active_blocks.append((base_abs + q, a_s, a_e))
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

        # PASS 6: atkprop
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

        # PASS 10: attach
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
                dist = abs(m_abs - mv_abs)
                if dist <= METER_PAIR_RANGE and (best_dist is None or dist < best_dist):
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
            for a_abs, a_s, a_e in active_blocks:
                dist = abs(a_abs - mv_abs)
                if dist <= ACTIVE_PAIR_RANGE and (best_dist is None or dist < best_dist):
                    best_dist = dist
                    mv_active_start = a_s
                    mv_active_end = a_e
            mv["active_start"] = mv_active_start
            mv["active_end"] = mv_active_end

            # damage
            mv_dmg = None
            mv_dflag = None
            best_dist = None
            for dmg_abs, dmg_val, dmg_flag in dmg_blocks:
                dist = abs(dmg_abs - mv_abs)
                if dist <= DAMAGE_PAIR_RANGE and (best_dist is None or dist < best_dist):
                    best_dist = dist
                    mv_dmg = dmg_val
                    mv_dflag = dmg_flag
            mv["damage"] = mv_dmg
            mv["damage_flag"] = mv_dflag

            # atk prop
            mv_prop = None
            best_dist = None
            for ap_abs, prop in atkprop_blocks:
                dist = abs(ap_abs - mv_abs)
                if dist <= ATKPROP_PAIR_RANGE and (best_dist is None or dist < best_dist):
                    best_dist = dist
                    mv_prop = prop
            mv["attack_property"] = mv_prop

            # hit reaction
            mv_react = None
            best_dist = None
            for hr_abs, code in hitreact_blocks:
                dist = abs(hr_abs - mv_abs)
                if dist <= HITREACTION_PAIR_RANGE and (best_dist is None or dist < best_dist):
                    best_dist = dist
                    mv_react = code
            mv["hit_reaction"] = mv_react

            # knockback
            mv_kb0 = None
            mv_kb1 = None
            mv_kb_traj = None
            best_dist = None
            for kb_abs, (kb0, kb1, traj) in kb_blocks:
                dist = abs(kb_abs - mv_abs)
                if dist <= KNOCKBACK_PAIR_RANGE and (best_dist is None or dist < best_dist):
                    best_dist = dist
                    mv_kb0 = kb0
                    mv_kb1 = kb1
                    mv_kb_traj = traj
            mv["kb0"] = mv_kb0
            mv["kb1"] = mv_kb1
            mv["kb_traj"] = mv_kb_traj

            # stuns — THIS was the one hurting 5A
            mv_hitstun = None
            mv_blockstun = None
            mv_hitstop = None
            best_dist = None
            for stun_abs, (hs, bs, hsop) in stun_blocks:
                dist = abs(stun_abs - mv_abs)
                if dist <= STUN_PAIR_RANGE and (best_dist is None or dist < best_dist):
                    best_dist = dist
                    mv_hitstun = hs
                    mv_blockstun = bs
                    mv_hitstop = hsop
            mv["hitstun"] = mv_hitstun
            mv["blockstun"] = mv_blockstun
            mv["hitstop"] = mv_hitstop

        # map cluster → slot
        slot_idx = cluster_to_slot[c_idx] if c_idx < len(cluster_to_slot) else c_idx
        slot_label, base_ptr, cid, cname = (
            slots_info[slot_idx] if slot_idx < len(slots_info) else (f"slot{slot_idx}", 0, None, "—")
        )

        # sort moves so ID 0 (5A) is always on top
        moves_sorted = sorted(
            moves,
            key=lambda m: (
                m.get("id") is None,
                m.get("id", 0xFFFF),
                m.get("abs", 0xFFFFFFFF),
            ),
        )

        result[slot_idx] = {
            "slot_label": slot_label,
            "char_name": cname,
            "moves": moves_sorted,
        }

    return result
