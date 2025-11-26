
import struct
from dolphin_io import hook, rbytes, rd32
from constants import MEM2_LO, MEM2_HI, SLOTS, CHAR_NAMES
from move_id_map import lookup_move_name


# ============================================================
# CONFIG CONSTANTS
# ============================================================

TAIL_PATTERN = b"\x00\x00\x00\x38\x01\x33\x00\x00"
CLUSTER_GAP = 0x4000
CLUSTER_PAD_BACK = 0x400

LOOKAHEAD_AFTER_HDR = 0x80

ANIM_HDR = [
    0x04, 0x01, 0x60, 0x00,
    0x00, 0x00, 0x01, 0xE8,
    0x3F, 0x00, 0x00, 0x00,
]

CMD_HDR = [
    0x04, 0x03, 0x60, 0x00,
    0x00, 0x00, 0x13, 0xCC,
    0x3F, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x08,
    0x01, 0x34, 0x00, 0x00, 0x00,
]
CMD_HDR_LEN = len(CMD_HDR)

AIR_HDR = [
    0x33, 0x33, 0x20, 0x00,
    0x01, 0x34, 0x00, 0x00, 0x00,
]
AIR_HDR_LEN = len(AIR_HDR)

SUPER_END_HDR = [
    0x04, 0x01, 0x60, 0x00,
    0x00, 0x00, 0x12, 0x18, 0x3F,
]

# Normal animation ID mapping
ANIM_MAP = {
    0x00: "5A", 0x01: "5B", 0x02: "5C",
    0x03: "2A", 0x04: "2B", 0x05: "2C",
    0x06: "6C", 0x08: "3C",
    0x09: "j.A", 0x0A: "j.B", 0x0B: "j.C",
    0x0E: "6B",
}
NORMAL_IDS = set(ANIM_MAP.keys())


# Meter defaults
DEFAULT_METER = {
    0x00: 0x32, 0x03: 0x32, 0x09: 0x32,
    0x01: 0x64, 0x04: 0x64, 0x0A: 0x64,
    0x02: 0x96, 0x05: 0x96, 0x0B: 0x96,
    0x06: 0x96, 0x08: 0x96, 0x0E: 0x96,
}
SPECIAL_DEFAULT_METER = 0xC8


# =============================
# Dynamic block patterns
# =============================

ACTIVE_HDR = [
    0x20, 0x35, 0x01, 0x20,
    0x3F, 0x00, 0x00, 0x00,
]
ACTIVE_TOTAL_LEN = 20

INLINE_ACTIVE_HDR = [
    0x3F, 0x00, 0x00, 0x00,  # 0–3
    None,                   # start frame
    0x11, 0x16, 0x20, 0x00,
    0x11, 0x22, 0x60, 0x00,
    0x00, 0x00, 0x00,
    None,                   # end frame
]
INLINE_ACTIVE_LEN = 17
INLINE_ACTIVE_OFF = 0xB0

DAMAGE_HDR = [0x35, 0x10, 0x20, 0x3F, 0x00]
DAMAGE_TOTAL_LEN = 16

ATKPROP_HDR = [
    0x04, 0x01, 0x60, 0x00,
    0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00,
]
ATKPROP_TOTAL_LEN = 17

HITREACTION_HDR = [
    0x04, 0x17, 0x60, 0x00,
    0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00,
    0x80, 0x04, 0x2F, 0x00,
    0x04, 0x15, 0x60, 0x00,
    0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00,
]
HITREACTION_TOTAL_LEN = len(HITREACTION_HDR)
HITREACTION_CODE_OFF = 28

KNOCKBACK_HDR = [
    0x35, None, None, 0x20,
    0x00, 0x00, 0x00, 0x00,
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


# Hitbox offsets inside move entry
HITBOX_OFF_X = 0x40
HITBOX_OFF_Y = 0x48


# ============================================================
# Utilities
# ============================================================

def rd_f32_be(buf, off):
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
    for p in range(start, end - 4 + 1):
        hi = buf[p]
        lo = buf[p+1]
        op = buf[p+2]
        fps = buf[p+3]
        if fps == 0x3C and op in (0x01, 0x04):
            aid = (hi << 8) | lo
            if 1 <= aid <= 0x0500:
                return aid
    return None


def find_all_tails(mem):
    offs = []
    p = 0
    while True:
        i = mem.find(TAIL_PATTERN, p)
        if i == -1:
            break
        offs.append(i)
        p = i + 1
    return offs


def cluster_tails(tails):
    if not tails:
        return []
    tails = sorted(tails)
    clusters = []
    cur = [tails[0]]
    for t in tails[1:]:
        if t - cur[-1] <= CLUSTER_GAP:
            cur.append(t)
        else:
            clusters.append(cur)
            cur = [t]
    clusters.append(cur)
    return clusters


def read_slots():
    out = []
    for label, ptr, tag in SLOTS:
        base = rd32(ptr)
        cid = None
        cname = "—"
        if base:
            cid = rd32(base + 0x14)
            if cid is not None:
                cname = CHAR_NAMES.get(cid, f"ID_{cid}")
        out.append((label, base, cid, cname))
    return out


# ============================================================
# Data block parsers
# ============================================================

def parse_active_frames(buf, pos):
    if pos + ACTIVE_TOTAL_LEN > len(buf):
        return None
    if not match_bytes(buf, pos, ACTIVE_HDR):
        return None
    start = buf[pos + 8]
    end = buf[pos + 16]
    return (start + 1, end + 1)


def parse_inline_active(buf, pos):
    if pos + INLINE_ACTIVE_LEN > len(buf):
        return None
    for i, b in enumerate(INLINE_ACTIVE_HDR):
        if b is None:
            continue
        if buf[pos + i] != b:
            return None
    s = buf[pos + 4]
    e = buf[pos + 16]
    if s == 0:
        return None
    if e < s:
        e = s
    return (s, e)


def parse_damage(buf, pos):
    if pos + DAMAGE_TOTAL_LEN > len(buf):
        return None
    if not match_bytes(buf, pos, DAMAGE_HDR):
        return None
    d0 = buf[pos+5]
    d1 = buf[pos+6]
    d2 = buf[pos+7]
    flag = buf[pos+15]
    return ((d0 << 16) | (d1 << 8) | d2, flag)


def parse_atkprop(buf, pos):
    if pos + ATKPROP_TOTAL_LEN > len(buf):
        return None
    if not match_bytes(buf, pos, ATKPROP_HDR):
        return None
    return buf[pos + len(ATKPROP_HDR)]


def parse_hitreaction(buf, pos):
    if pos + HITREACTION_TOTAL_LEN > len(buf):
        return None
    if not match_bytes(buf, pos, HITREACTION_HDR):
        return None
    x = buf[pos+28]
    y = buf[pos+29]
    z = buf[pos+30]
    return (x << 16) | (y << 8) | z


def parse_knockback(buf, pos):
    if pos + KNOCKBACK_TOTAL_LEN > len(buf):
        return None
    # No strict pattern matching here (variable bytes)
    kb0 = buf[pos+1]
    kb1 = buf[pos+2]
    traj = buf[pos+12]
    return (kb0, kb1, traj)


def parse_stun(buf, pos):
    if pos + STUN_TOTAL_LEN > len(buf):
        return None
    if not match_bytes(buf, pos, STUN_HDR):
        return None
    hitstun = buf[pos+15]
    blockstun = buf[pos+31]
    hitstop = buf[pos+38]
    return (hitstun, blockstun, hitstop)


def pick_best_block(mv_abs, blocks, rng=PAIR_RANGE):
    best = None
    best_dist = None
    for addr, data in blocks:
        if addr >= mv_abs:
            d = addr - mv_abs
            if d <= rng and (best is None or d < best_dist):
                best = (addr, data)
                best_dist = d
    if best:
        return best
    for addr, data in blocks:
        d = abs(addr - mv_abs)
        if d <= rng and (best is None or d < best_dist):
            best = (addr, data)
            best_dist = d
    return best
# ============================================================
# MAIN SCAN
# ============================================================

def scan_once():
    hook()

    # Which characters are loaded
    slots_info = read_slots()

    # Read the entire MEM2 region once
    mem = rbytes(MEM2_LO, MEM2_HI - MEM2_LO)

    # Find tail markers → clusters → character blocks
    tails = find_all_tails(mem)
    clusters = cluster_tails(tails)

    # Slot association based on your existing mapping
    cluster_to_slot = [0, 2, 1, 3]

    result = []
    max_chars = min(4, len(clusters))

    # Initialize result array
    for _ in range(max_chars):
        result.append({
            "slot_label": "",
            "char_name": "",
            "moves": [],
        })

    # ========================================================
    # FOR EACH CHARACTER CLUSTER
    # ========================================================
    for c_idx in range(max_chars):

        tails_in_cluster = clusters[c_idx]
        start_off = tails_in_cluster[0]

        # Pull back a bit for the beginning of the table
        start_off = max(0, start_off - CLUSTER_PAD_BACK)

        # End bound
        if c_idx + 1 < len(clusters):
            end_off = clusters[c_idx + 1][0]
        else:
            end_off = min(len(mem), start_off + 0x8000)

        buf = mem[start_off:end_off]
        base_abs = MEM2_LO + start_off

        # ------------------------------
        # PASS 1: FIND ALL MOVE ANCHORS
        # ------------------------------
        moves = []
        i = 0

        while i < len(buf):

            # SUPER END
            if match_bytes(buf, i, SUPER_END_HDR):
                moves.append({
                    "kind": "super",
                    "abs": base_abs + i,
                    "id": None
                })
                i += len(SUPER_END_HDR)
                continue

            # AIR → ANIM_HDR
            if match_bytes(buf, i, AIR_HDR):
                s0 = i + AIR_HDR_LEN
                s1 = min(s0 + LOOKAHEAD_AFTER_HDR, len(buf))
                for p in range(s0, s1):
                    if match_bytes(buf, p, ANIM_HDR):
                        aid = get_anim_id_after_hdr(buf, p)
                        kind = ("normal" if (aid and (aid & 0xFF) in NORMAL_IDS)
                                else "special")
                        moves.append({
                            "kind": kind,
                            "abs": base_abs + p,
                            "id": aid
                        })
                        break
                i += AIR_HDR_LEN
                continue

            # CMD → ANIM_HDR
            if match_bytes(buf, i, CMD_HDR):
                s0 = i + CMD_HDR_LEN + 3
                s1 = min(s0 + LOOKAHEAD_AFTER_HDR, len(buf))
                for p in range(s0, s1):
                    if match_bytes(buf, p, ANIM_HDR):
                        aid = get_anim_id_after_hdr(buf, p)
                        kind = ("normal" if (aid and (aid & 0xFF) in NORMAL_IDS)
                                else "special")
                        moves.append({
                            "kind": kind,
                            "abs": base_abs + p,
                            "id": aid
                        })
                        break
                i += CMD_HDR_LEN
                continue

            # DIRECT ANIM
            if match_bytes(buf, i, ANIM_HDR):
                aid = get_anim_id_after_hdr(buf, i)
                kind = ("normal" if (aid and (aid & 0xFF) in NORMAL_IDS)
                        else "special")
                moves.append({
                    "kind": kind,
                    "abs": base_abs + i,
                    "id": aid
                })
                i += len(ANIM_HDR)
                continue

            # ==========================================
            # NEW OPTION A: SPECIAL FRAGMENT DETECTOR
            # 01 XX 01 3C, where 01 <= XX <= 1E
            # ==========================================
            if i + 4 <= len(buf):
                if (buf[i] == 0x01 and
                    buf[i+2] == 0x01 and
                    buf[i+3] == 0x3C):

                    lo = buf[i+1]

                    if 0x01 <= lo <= 0x1E:
                        aid = 0x0100 | lo   # 0111, 0112, 0113 ...
                        moves.append({
                            "kind": "special",
                            "abs": base_abs + i,
                            "id": aid
                        })
                        i += 4
                        continue

            # Default step
            i += 1

        # =======================================================
        # PASS 2..9: COLLECT ALL BLOCKS (meter, active, dmg, etc)
        # =======================================================

        # METER
        METER_HDR = [
            0x34, 0x04, 0x00, 0x20,
            0x00, 0x00, 0x00, 0x03,
            0x00, 0x00, 0x00, 0x00,
            0x36, 0x43, 0x00, 0x20,
            0x00, 0x00, 0x00,
            0x36, 0x43, 0x00, 0x20,
            0x00, 0x00, 0x00,
        ]
        METER_TOTAL_LEN = len(METER_HDR)+5

        meters = []
        p = 0
        while p < len(buf):
            if match_bytes(buf, p, METER_HDR):
                if p + METER_TOTAL_LEN <= len(buf):
                    meters.append((base_abs + p, buf[p + len(METER_HDR)]))
                p += len(METER_HDR)
                continue
            p += 1

        # ACTIVE
        active_blocks = []
        p = 0
        while p < len(buf):
            if match_bytes(buf, p, ACTIVE_HDR):
                af = parse_active_frames(buf, p)
                if af:
                    active_blocks.append((base_abs + p, af))
                p += ACTIVE_TOTAL_LEN
                continue
            p += 1

        # INLINE ACTIVE
        inline_active_blocks = []
        p = 0
        while p < len(buf):
            af = parse_inline_active(buf, p)
            if af:
                inline_active_blocks.append((base_abs + p, af))
                p += INLINE_ACTIVE_LEN
                continue
            p += 1

        # DAMAGE
        dmg_blocks = []
        p = 0
        while p < len(buf):
            d = parse_damage(buf, p)
            if d:
                dmg_blocks.append((base_abs+p, d))
                p += DAMAGE_TOTAL_LEN
                continue
            p += 1

        # ATKPROP
        atkprop_blocks = []
        p = 0
        while p < len(buf):
            d = parse_atkprop(buf, p)
            if d is not None:
                atkprop_blocks.append((base_abs+p, d))
                p += ATKPROP_TOTAL_LEN
                continue
            p += 1

        # HIT REACTION
        hitreact_blocks = []
        p = 0
        while p < len(buf):
            d = parse_hitreaction(buf, p)
            if d is not None:
                hitreact_blocks.append((base_abs+p, d))
                p += HITREACTION_TOTAL_LEN
                continue
            p += 1

        # KNOCKBACK
        kb_blocks = []
        p = 0
        while p < len(buf):
            d = parse_knockback(buf, p)
            if d:
                kb_blocks.append((base_abs+p, d))
                p += KNOCKBACK_TOTAL_LEN
                continue
            p += 1

        # STUN
        stun_blocks = []
        p = 0
        while p < len(buf):
            d = parse_stun(buf, p)
            if d:
                stun_blocks.append((base_abs+p, d))
                p += STUN_TOTAL_LEN
                continue
            p += 1

        # =======================================================
        # PASS 10: ATTACH DATA TO MOVES
        # =======================================================

        for mv in moves:
            aid = mv["id"]
            mv_abs = mv["abs"]
            aid_low = (aid & 0xFF) if aid is not None else None

            # Meter
            if mv["kind"] == "normal":
                mv["meter"] = DEFAULT_METER.get(aid_low)
            else:
                mv["meter"] = SPECIAL_DEFAULT_METER

            mblk = pick_best_block(mv_abs, meters)
            mv["meter_addr"] = None
            if mblk:
                mv["meter"] = mblk[1]
                mv["meter_addr"] = mblk[0]

            # Active
            mv["active_start"] = None
            mv["active_end"] = None
            mv["active_addr"] = None
            ablk = pick_best_block(mv_abs, active_blocks)
            if ablk:
                mv["active_start"], mv["active_end"] = ablk[1]
                mv["active_addr"] = ablk[0]

            # INLINE ACTIVE 2
            mv["active2_start"] = None
            mv["active2_end"] = None
            mv["active2_addr"] = None

            rel = mv_abs - base_abs
            inline_off = rel + INLINE_ACTIVE_OFF
            if 0 <= inline_off < len(buf)-INLINE_ACTIVE_LEN:
                a2 = parse_inline_active(buf, inline_off)
                if a2:
                    mv["active2_start"], mv["active2_end"] = a2
                    mv["active2_addr"] = base_abs + inline_off

            if mv["active2_start"] is None:
                ablk = pick_best_block(mv_abs, inline_active_blocks)
                if ablk:
                    mv["active2_start"], mv["active2_end"] = ablk[1]
                    mv["active2_addr"] = ablk[0]

            # Damage
            mv["damage"] = None
            mv["damage_flag"] = None
            mv["damage_addr"] = None

            dblk = pick_best_block(mv_abs, dmg_blocks)
            if dblk:
                mv["damage"], mv["damage_flag"] = dblk[1]
                mv["damage_addr"] = dblk[0]

            # ATKPROP
            mv["attack_property"] = None
            mv["atkprop_addr"] = None

            apblk = pick_best_block(mv_abs, atkprop_blocks)
            if apblk:
                mv["attack_property"] = apblk[1]
                mv["atkprop_addr"] = apblk[0]

            # HIT REACTION
            mv["hit_reaction"] = None
            mv["hit_reaction_addr"] = None

            hrblk = pick_best_block(mv_abs, hitreact_blocks)
            if hrblk:
                mv["hit_reaction"] = hrblk[1]
                mv["hit_reaction_addr"] = hrblk[0] + HITREACTION_CODE_OFF

            # KNOCKBACK
            mv["kb0"] = None
            mv["kb1"] = None
            mv["kb_traj"] = None
            mv["knockback_addr"] = None

            kbblk = pick_best_block(mv_abs, kb_blocks)
            if kbblk:
                mv["kb0"], mv["kb1"], mv["kb_traj"] = kbblk[1]
                mv["knockback_addr"] = kbblk[0]

            # STUN
            mv["hitstun"] = None
            mv["blockstun"] = None
            mv["hitstop"] = None
            mv["stun_addr"] = None

            sblk = pick_best_block(mv_abs, stun_blocks)
            if sblk:
                mv["hitstun"], mv["blockstun"], mv["hitstop"] = sblk[1]
                mv["stun_addr"] = sblk[0]

            # Hitbox dims
            mv["hb_x"] = None
            mv["hb_y"] = None

            off_x = rel + HITBOX_OFF_X
            off_y = rel + HITBOX_OFF_Y

            if off_x + 4 <= len(buf):
                try:
                    mv["hb_x"] = rd_f32_be(buf, off_x)
                except:
                    pass
            if off_y + 4 <= len(buf):
                try:
                    mv["hb_y"] = rd_f32_be(buf, off_y)
                except:
                    pass

            # Advantage
            total_frames = mv.get("speed") or 0x3C
            a_end = mv.get("active_end")
            if a_end:
                recovery = max(0, total_frames - a_end)
            else:
                recovery = 12
            hs = mv.get("hitstun") or 0
            bs = mv.get("blockstun") or 0
            mv["adv_hit"] = hs - recovery
            mv["adv_block"] = bs - recovery

            # Human-readable name
            if aid is None:
                mv["move_name"] = "anim_--"
            else:
                name = lookup_move_name(aid)
                if not name:
                    lo = (aid & 0xFF)
                    name = ANIM_MAP.get(lo)
                mv["move_name"] = name if name else f"anim_{aid:04X}"

        # =======================================================
        # SORT MOVES
        # =======================================================

        def sort_key(m):
            aid = m["id"]
            if aid is None:
                return (2, 0xFFFF, m["abs"])
            elif aid >= 0x0100:
                return (0, aid, m["abs"])
            else:
                return (1, aid, m["abs"])

        moves_sorted = sorted(moves, key=sort_key)

        # Assign to slot
        slot_idx = cluster_to_slot[c_idx] if c_idx < len(cluster_to_slot) else c_idx
        if slot_idx < len(slots_info):
            slot_label, base_ptr, cid, cname = slots_info[slot_idx]
        else:
            slot_label, cname = f"slot{slot_idx}", "—"

        result[slot_idx] = {
            "slot_label": slot_label,
            "char_name": cname,
            "moves": moves_sorted,
        }

    return result
