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

TAIL_PATTERN = b"\x00\x00\x00\x38\x01\x33\x00\x00"
CLUSTER_GAP = 0x4000
CLUSTER_PAD_BACK = 0x400
HITREACTION_HDR = [
    0x04, 0x17, 0x60, 0x00, 0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00, 0x80, 0x04, 0x2F, 0x00,
    0x04, 0x15, 0x60, 0x00, 0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00,
]
HITREACTION_TOTAL_LEN = 33
HITREACTION_CODE_OFF = len(HITREACTION_HDR)  # 28

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

ANIM_ID_PATTERN = [None, None, None, 0x3C]  # hi, lo, opcode(1 or 4), 0x3C

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

# Inline active block pattern: 3F 00 00 00 XX 11 16 20 00 11 22 60 00 00 00 YY
# Total 16 bytes - confirmed from hex dump at 908aeea0+F to 908aeeb0+E
INLINE_ACTIVE_OFF = 0xB0
INLINE_ACTIVE_HDR = [
    0x3F, 0x00, 0x00, 0x00,  # [0-3]: 3F 00 00 00
    None,                     # [4]: start frame (XX)
    0x11, 0x16, 0x20, 0x00,   # [5-8]: 11 16 20 00
    0x11, 0x22, 0x60, 0x00,   # [9-12]: 11 22 60 00
    0x00, 0x00, 0x00,         # [13-15]: 00 00 00
    None,                     # [16]: end frame (YY)
]
INLINE_ACTIVE_LEN = 17
INLINE_ACTIVE_PAIR_RANGE = PAIR_RANGE


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
    start = hdr_pos + len(ANIM_HDR)
    end = min(start + LOOKAHEAD_AFTER_HDR, len(buf))
    pat_len = 4

    for p in range(start, end - pat_len + 1):
        hi = buf[p]
        lo = buf[p + 1]
        op = buf[p + 2]
        fps = buf[p + 3]

        if fps != 0x3C:
            continue
        if op not in (0x01, 0x04):
            continue

        anim_id = (hi << 8) | lo
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
    op = buf[start + len(ANIM_SPEED_HDR) + 1]
    fps = buf[start + len(ANIM_SPEED_HDR) + 2]
    if fps != 0x3C or op not in (0x01, 0x04):
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


def parse_inline_active(buf: bytes, start: int):
    """
    Parse the 'inline' active block:
      3F 00 00 00 XX 11 16 20 00 11 22 60 00 00 00 YY
    
    We treat XX as the start frame and YY as the end frame.
    Returns (start, end) or (None, None) if it doesn't match.
    """
    if start + INLINE_ACTIVE_LEN > len(buf):
        return None, None

    # Check the pattern
    for i, expected_byte in enumerate(INLINE_ACTIVE_HDR):
        if expected_byte is None:
            continue  # Wildcard position
        if buf[start + i] != expected_byte:
            return None, None

    # Extract the frame values
    start_frame = buf[start + 4]   # Position 4: XX
    end_frame = buf[start + 16]    # Position 16: YY

    # Sanity checks
    if start_frame == 0:
        return None, None
    if end_frame < start_frame:
        end_frame = start_frame

    return start_frame, end_frame


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
                        kind = "normal" if (aid is not None and (aid & 0xFF) in NORMAL_IDS) else "special"
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
                        kind = "normal" if (aid is not None and (aid & 0xFF) in NORMAL_IDS) else "special"
                        moves.append({"kind": kind, "abs": base_abs + p, "id": aid})
                        break
                i += CMD_HDR_LEN
                continue
            if match_bytes(buf, i, ANIM_HDR):
                aid = get_anim_id_after_hdr(buf, i)
                kind = "normal" if (aid is not None and (aid & 0xFF) in NORMAL_IDS) else "special"
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

        # PASS 4: active frames (table-based)
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

        # PASS 4b: inline active frames (Active 2) - scan entire buffer
        inline_active_blocks = []
        q2 = 0
        found_count = 0
        while q2 < len(buf):
            a2_s, a2_e = parse_inline_active(buf, q2)
            if a2_s is not None:
                inline_active_blocks.append((base_abs + q2, (a2_s, a2_e)))
                found_count += 1
                q2 += INLINE_ACTIVE_LEN
                continue
            q2 += 1

        print(f"Cluster {c_idx}: Found {found_count} inline active blocks")

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

        # PASS 10: attach data + advantage + HB + STORE ADDRESSES
        for mv in moves:
            aid = mv["id"]
            mv_abs = mv["abs"]
            aid_low = (aid & 0xFF) if aid is not None else None

            # meter
            if mv["kind"] == "normal":
                mv["meter"] = DEFAULT_METER.get(aid_low)
            elif mv["kind"] == "special":
                mv["meter"] = SPECIAL_DEFAULT_METER
            else:
                mv["meter"] = None
            mv["meter_addr"] = None
            mblk = pick_best_block(mv_abs, meters, METER_PAIR_RANGE)
            if mblk:
                mv["meter"] = mblk[1]
                mv["meter_addr"] = mblk[0]

            # animation speed
            mv["speed"] = None
            for b_abs, (sa_id, sa_spd) in speed_blocks:
                if aid_low is not None and sa_id == aid_low:
                    mv["speed"] = sa_spd
                    break

            # active (table)
            mv["active_start"] = None
            mv["active_end"] = None
            mv["active_addr"] = None
            ablk = pick_best_block(mv_abs, active_blocks, ACTIVE_PAIR_RANGE)
            if ablk:
                a_s, a_e = ablk[1]
                mv["active_start"] = a_s
                mv["active_end"] = a_e
                mv["active_addr"] = ablk[0]

            # inline active block (Active 2)
            # Method 1: try fixed offset first
            rel = mv_abs - base_abs
            inline_off = rel + INLINE_ACTIVE_OFF

            mv["active2_start"] = None
            mv["active2_end"] = None
            mv["active2_addr"] = None
            mv["active_inline_start"] = None
            mv["active_inline_end"] = None

            if 0 <= inline_off < len(buf) - INLINE_ACTIVE_LEN:
                a2_s, a2_e = parse_inline_active(buf, inline_off)
                if a2_s is not None:
                    mv["active2_start"] = a2_s
                    mv["active2_end"] = a2_e
                    mv["active2_addr"] = base_abs + inline_off
                    mv["active_inline_start"] = a2_s
                    mv["active_inline_end"] = a2_e
                    aid_str = f"0x{aid:04X}" if aid is not None else "None"
                    print(f"  Move 0x{mv_abs:08X} (ID={aid_str}): Found inline active {a2_s}-{a2_e} at offset 0x{INLINE_ACTIVE_OFF:X}")

            # Method 2: if not found at fixed offset, try scanned blocks
            if mv["active2_start"] is None:
                iablk = pick_best_block(mv_abs, inline_active_blocks, INLINE_ACTIVE_PAIR_RANGE)
                if iablk:
                    a2_s, a2_e = iablk[1]
                    mv["active2_start"] = a2_s
                    mv["active2_end"] = a2_e
                    mv["active2_addr"] = iablk[0]
                    mv["active_inline_start"] = a2_s
                    mv["active_inline_end"] = a2_e
                    aid_str = f"0x{aid:04X}" if aid is not None else "None"
                    print(f"  Move 0x{mv_abs:08X} (ID={aid_str}): Found inline active {a2_s}-{a2_e} via scan")

            # damage
            mv["damage"] = None
            mv["damage_flag"] = None
            mv["damage_addr"] = None
            dblk = pick_best_block(mv_abs, dmg_blocks, DAMAGE_PAIR_RANGE)
            if dblk:
                dmg_val, dmg_flag = dblk[1]
                mv["damage"] = dmg_val
                mv["damage_flag"] = dmg_flag
                mv["damage_addr"] = dblk[0]

            # attack property
            mv["attack_property"] = None
            mv["atkprop_addr"] = None
            apblk = pick_best_block(mv_abs, atkprop_blocks, ATKPROP_PAIR_RANGE)
            if apblk:
                mv["attack_property"] = apblk[1]
                mv["atkprop_addr"] = apblk[0]

            # hit reaction
            mv["hit_reaction"] = None
            mv["hit_reaction_addr"] = None
            hrblk = pick_best_block(mv_abs, hitreact_blocks, HITREACTION_PAIR_RANGE)
            if hrblk:
                mv["hit_reaction"] = hrblk[1]
                # hrblk[0] = start of 04 17 60 ... header
                # XX YY ZZ live immediately after that header
                mv["hit_reaction_addr"] = hrblk[0] + HITREACTION_CODE_OFF



            # knockback
            mv["kb0"] = None
            mv["kb1"] = None
            mv["kb_traj"] = None
            mv["knockback_addr"] = None
            kbblk = pick_best_block(mv_abs, kb_blocks, KNOCKBACK_PAIR_RANGE)
            if kbblk:
                kb0, kb1, traj = kbblk[1]
                mv["kb0"] = kb0
                mv["kb1"] = kb1
                mv["kb_traj"] = traj
                mv["knockback_addr"] = kbblk[0]

            # stuns
            mv["hitstun"] = None
            mv["blockstun"] = None
            mv["hitstop"] = None
            mv["stun_addr"] = None
            sblk = pick_best_block(mv_abs, stun_blocks, STUN_PAIR_RANGE)
            if sblk:
                hs, bs, hstop = sblk[1]
                mv["hitstun"] = hs
                mv["blockstun"] = bs
                mv["hitstop"] = hstop
                mv["stun_addr"] = sblk[0]

            # hitbox sizes
            mv["hb_x"] = None
            mv["hb_y"] = None
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

            # advantage calc (still based on table active_end, not inline)
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

        def _move_sort_key(m):
            aid = m.get("id")
            if aid is None:
                group = 2
                order = 0xFFFF
            elif aid >= 0x100:
                group = 0
                order = aid
            else:
                group = 1
                order = aid
            return (group, order, m.get("abs", 0xFFFFFFFF))

        moves_sorted = sorted(moves, key=_move_sort_key)

        result[slot_idx] = {
            "slot_label": slot_label,
            "char_name": cname,
            "moves": moves_sorted,
        }

    return result