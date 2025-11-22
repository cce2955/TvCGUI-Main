# scan_normals_all.py
#
# Scans MEM2 for TvC move tables (up to 4 characters),
# extracts move info, and computes *estimated* frame advantage.
# Now also stores the ADDRESSES of each data block for editing.
#
# main.py will call scan_once() and show it.

import struct
from dolphin_io import hook, rbytes, rd32
from constants import MEM2_LO, MEM2_HI, SLOTS, CHAR_NAMES

# ... [Keep all your existing pattern definitions: TAIL_PATTERN, ANIM_HDR, etc.] ...
# I'll include them for completeness:

TAIL_PATTERN = b"\x00\x00\x00\x38\x01\x33\x00\x00"
CLUSTER_GAP = 0x4000
CLUSTER_PAD_BACK = 0x400

ANIM_HDR = [
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x01, 0xE8,
    0x3F, 0x00, 0x00, 0x00,
]

CMD_HDR = [
    0x04, 0x03, 0x60, 0x00, 0x00, 0x00, 0x13, 0xCC,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x08,
    0x01, 0x34, 0x00, 0x00, 0x00,
]
CMD_HDR_LEN = len(CMD_HDR)

AIR_HDR = [
    0x33, 0x33, 0x20, 0x00, 0x01, 0x34, 0x00, 0x00, 0x00,
]
AIR_HDR_LEN = len(AIR_HDR)

SUPER_END_HDR = [
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x12, 0x18, 0x3F,
]


ANIM_ID_PATTERN = [None, None, 0x01, 0x3C]

LOOKAHEAD_AFTER_HDR = 0x80

METER_HDR = [
    0x34, 0x04, 0x00, 0x20, 0x00, 0x00, 0x00, 0x03,
    0x00, 0x00, 0x00, 0x00,
    0x36, 0x43, 0x00, 0x20, 0x00, 0x00, 0x00,
    0x36, 0x43, 0x00, 0x20, 0x00, 0x00, 0x00,
]
METER_TOTAL_LEN = len(METER_HDR) + 5

ANIM_SPEED_HDR = [
    0x04, 0x01, 0x02, 0x3F, 0x00, 0x00, 0x01,
]

ACTIVE_HDR = [
    0x20, 0x35, 0x01, 0x20, 0x3F, 0x00, 0x00, 0x00,
]
ACTIVE_TOTAL_LEN = 20

DAMAGE_HDR = [
    0x35, 0x10, 0x20, 0x3F, 0x00,
]
DAMAGE_TOTAL_LEN = 16

ATKPROP_HDR = [
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
]
ATKPROP_TOTAL_LEN = 17

HITREACTION_HDR = [
    0x04, 0x17, 0x60, 0x00, 0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00, 0x80, 0x04, 0x2F, 0x00,
    0x04, 0x15, 0x60, 0x00, 0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00,
]
HITREACTION_TOTAL_LEN = 33

KNOCKBACK_HDR = [
    0x35, None, None, 0x20, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00,
]
KNOCKBACK_TOTAL_LEN = 20

STUN_HDR = [
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x54,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, None,
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x58,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, None,
    0x33, 0x32, 0x00, 0x20, 0x00, 0x00, 0x00, None,
    0x04, 0x15, 0x60,
]
STUN_TOTAL_LEN = 43

PAIR_RANGE = 0x600
METER_PAIR_RANGE = PAIR_RANGE
ACTIVE_PAIR_RANGE = PAIR_RANGE
DAMAGE_PAIR_RANGE = PAIR_RANGE
ATKPROP_PAIR_RANGE = PAIR_RANGE
HITREACTION_PAIR_RANGE = PAIR_RANGE
KNOCKBACK_PAIR_RANGE = PAIR_RANGE
STUN_PAIR_RANGE = PAIR_RANGE

HITBOX_OFF_X = 0x40
HITBOX_OFF_Y = 0x48

ANIM_MAP = {
    0x00: "5A", 0x01: "5B", 0x02: "5C", 0x03: "2A", 0x04: "2B", 0x05: "2C",
    0x06: "6C", 0x08: "3C", 0x09: "j.A", 0x0A: "j.B", 0x0B: "j.C", 0x0E: "6B",
    0x14: "donkey/dash-ish", 0x15: "Tatsu L", 0x16: "Tatsu M (first hit)",
    0x17: "Tatsu M (second/third hit)", 0x18: "Tatsu H (first hit)",
    0x19: "Tatsu H (last three hits)", 0x1A: "Tatsu L (air)",
    0x1B: "Tatsu L (air, second hit)", 0x1C: "Tatsu M (air, first hit)",
    0x1D: "Tatsu M (air, second/third hit)", 0x1E: "Tatsu H (air, first)",
    0x1F: "Tatsu H (air, rest)", 0x20: "Shoryu L", 0x21: "Shoryu M (second hit)",
    0x22: "Shoryu M (first hit)", 0x23: "Shoryu H (first hit)",
    0x24: "Shoryu H (second hit)", 0x25: "Donkey L", 0x26: "Donkey M",
    0x27: "Donkey H", 0x28: "Tatsu Super", 0x29: "ShinSho (hit 1)",
    0x2A: "ShinSho (hit 2)", 0x2B: "ShinSho (hit 3/4?)", 0x2C: "ShinSho (last?)",
}
NORMAL_IDS = set(ANIM_MAP.keys())

DEFAULT_METER = {
    0x00: 0x32, 0x03: 0x32, 0x09: 0x32,
    0x01: 0x64, 0x04: 0x64, 0x0A: 0x64,
    0x02: 0x96, 0x05: 0x96, 0x0B: 0x96,
    0x06: 0x96, 0x08: 0x96, 0x0E: 0x96, 0x14: 0x96,
}
SPECIAL_DEFAULT_METER = 0xC8

# Helper functions
def rd_f32_be(buf: bytes, off: int):
    return struct.unpack(">f", buf[off:off+4])[0]

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
    """
    NEW: Extract 16-bit animation ID from pattern:
        hi  lo  01  3C
    """
    start = hdr_pos + len(ANIM_HDR)
    end = min(start + LOOKAHEAD_AFTER_HDR, len(buf))
    pat_len = 4

    for p in range(start, end - pat_len + 1):
        if match_bytes(buf, p, ANIM_ID_PATTERN):
            hi = buf[p]
            lo = buf[p + 1]
            anim_id = (hi << 8) | lo

            # Reject impossible IDs
            if anim_id == 0:
                continue
            if anim_id < 0x0001:
                continue
            if anim_id > 0x0500: 
                continue

            return anim_id

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
    if buf[start + len(ANIM_SPEED_HDR) + 1] != 0x01 or buf[start + len(ANIM_SPEED_HDR) + 2] != 0x3C:
        return None, None
    tail_pat = [0x33, 0x35, 0x20, 0x3F, 0x00, 0x00, 0x00]
    s_from = start + 16
    s_to = min(start + 64, len(buf))
    for p in range(s_from, s_to - len(tail_pat) + 1):
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
    d0 = buf[start + 5]; d1 = buf[start + 6]; d2 = buf[start + 7]
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
    xx = buf[start + 28]; yy = buf[start + 29]; zz = buf[start + 30]
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
    hitstun = buf[start + 15]
    blockstun = buf[start + 31]
    hitstop = buf[start + 38]
    return hitstun, blockstun, hitstop

def pick_best_block(mv_abs, blocks, pair_range):
    best = None
    best_dist = None
    for b_abs, data in blocks:
        if b_abs >= mv_abs:
            dist = b_abs - mv_abs
            if dist <= pair_range and (best is None or dist < best_dist):
                best = (b_abs, data)
                best_dist = dist
    if best is not None:
        return best
    for b_abs, data in blocks:
        dist = abs(b_abs - mv_abs)
        if dist <= pair_range and (best is None or dist < best_dist):
            best = (b_abs, data)
            best_dist = dist
    return best

def scan_once():
    hook()
    slots_info = read_slots()
    mem = rbytes(MEM2_LO, MEM2_HI - MEM2_LO)
    tails = find_all_tails(mem)
    clusters = cluster_tails(tails)
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

        # PASS 1: detect moves
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

        # PASS 3: animation speeds
        speed_blocks = []
        k = 0
        while k < len(buf):
            aid, spd = parse_anim_speed(buf, k)
            if aid is not None:
                speed_blocks.append((base_abs + k, (aid, spd)))
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
                    active_blocks.append((base_abs + q, (a_s, a_e)))
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
                    dmg_blocks.append((base_abs + d, (dmg_val, dmg_flag)))
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

        # PASS 10: attach + advantage + HB + STORE ADDRESSES
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
            mv["meter_addr"] = None  # <-- NEW: store address
            mblk = pick_best_block(mv_abs, meters, METER_PAIR_RANGE)
            if mblk:
                mv["meter"] = mblk[1]
                mv["meter_addr"] = mblk[0]  # <-- NEW

            # animation speed
            mv["speed"] = None
            for b_abs, (sa_id, sa_spd) in speed_blocks:
                if sa_id == aid:
                    mv["speed"] = sa_spd
                    break

            # active
            mv["active_start"] = None
            mv["active_end"] = None
            mv["active_addr"] = None  # <-- NEW
            ablk = pick_best_block(mv_abs, active_blocks, ACTIVE_PAIR_RANGE)
            if ablk:
                a_s, a_e = ablk[1]
                mv["active_start"] = a_s
                mv["active_end"] = a_e
                mv["active_addr"] = ablk[0]  # <-- NEW

            # damage
            mv["damage"] = None
            mv["damage_flag"] = None
            mv["damage_addr"] = None  # <-- NEW
            dblk = pick_best_block(mv_abs, dmg_blocks, DAMAGE_PAIR_RANGE)
            if dblk:
                dmg_val, dmg_flag = dblk[1]
                mv["damage"] = dmg_val
                mv["damage_flag"] = dmg_flag
                mv["damage_addr"] = dblk[0]  # <-- NEW

            # attack property
            mv["attack_property"] = None
            mv["atkprop_addr"] = None  # <-- NEW
            apblk = pick_best_block(mv_abs, atkprop_blocks, ATKPROP_PAIR_RANGE)
            if apblk:
                mv["attack_property"] = apblk[1]
                mv["atkprop_addr"] = apblk[0]  # <-- NEW

            # hit reaction
            mv["hit_reaction"] = None
            hrblk = pick_best_block(mv_abs, hitreact_blocks, HITREACTION_PAIR_RANGE)
            if hrblk:
                mv["hit_reaction"] = hrblk[1]

            # knockback
            mv["kb0"] = None
            mv["kb1"] = None
            mv["kb_traj"] = None
            mv["knockback_addr"] = None  # <-- NEW
            kbblk = pick_best_block(mv_abs, kb_blocks, KNOCKBACK_PAIR_RANGE)
            if kbblk:
                kb0, kb1, traj = kbblk[1]
                mv["kb0"] = kb0
                mv["kb1"] = kb1
                mv["kb_traj"] = traj
                mv["knockback_addr"] = kbblk[0]  # <-- NEW

            # stuns
            mv["hitstun"] = None
            mv["blockstun"] = None
            mv["hitstop"] = None
            mv["stun_addr"] = None  # <-- NEW
            sblk = pick_best_block(mv_abs, stun_blocks, STUN_PAIR_RANGE)
            if sblk:
                hs, bs, hstop = sblk[1]
                mv["hitstun"] = hs
                mv["blockstun"] = bs
                mv["hitstop"] = hstop
                mv["stun_addr"] = sblk[0]  # <-- NEW

            # hitbox sizes
            mv["hb_x"] = None
            mv["hb_y"] = None
            rel = mv_abs - base_abs
            off_x = rel + HITBOX_OFF_X
            off_y = rel + HITBOX_OFF_Y
            if off_x + 4 <= len(buf):
                try:
                    mv["hb_x"] = rd_f32_be(buf, off_x)
                except Exception:
                    pass
            if off_y + 4 <= len(buf):
                try:
                    mv["hb_y"] = rd_f32_be(buf, off_y)
                except Exception:
                    pass

            # advantage calc
            total_frames = mv.get("speed") or 0x3C
            a_end = mv.get("active_end")
            if a_end is not None:
                recovery = total_frames - a_end
                if recovery < 0:
                    recovery = 0
            else:
                recovery = 12
            hs = mv.get("hitstun") or 0
            bs = mv.get("blockstun") or 0
            mv["adv_hit"] = hs - recovery
            mv["adv_block"] = bs - recovery

        # map cluster to actual slot
        slot_idx = cluster_to_slot[c_idx] if c_idx < len(cluster_to_slot) else c_idx
        slot_label, base_ptr, cid, cname = (
            slots_info[slot_idx] if slot_idx < len(slots_info) else (f"slot{slot_idx}", 0, None, "—")
        )

        moves_sorted = sorted(
            moves,
            key=lambda m: ((m["id"] is None), m.get("id", 0xFFFF), m.get("abs", 0xFFFFFFFF))
        )

        result[slot_idx] = {
            "slot_label": slot_label,
            "char_name": cname,
            "moves": moves_sorted,
        }

    return result