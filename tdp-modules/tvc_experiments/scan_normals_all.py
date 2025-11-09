# scan_normals_all.py
#
# noisy version – prints every step so we know where it dies

import sys

print("[scan] starting…")

try:
    from dolphin_io import hook, rbytes, rd32
    from constants import MEM2_LO, MEM2_HI, SLOTS, CHAR_NAMES
    print("[scan] dolphin modules imported")
except Exception as e:
    print("[scan] FAILED to import dolphin modules:", e)
    sys.exit(1)

# ------------------------------------------------------------
# patterns / tables
# ------------------------------------------------------------
TAIL_PATTERN = b"\x00\x00\x00\x38\x01\x33\x00\x00"
CLUSTER_GAP = 0x4000

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

ANIM_ID_PATTERN = [0x01, None, 0x01, 0x3C]
LOOKAHEAD_AFTER_HDR = 0x80

METER_HDR = [
    0x34, 0x04, 0x00, 0x20, 0x00, 0x00, 0x00, 0x03,
    0x00, 0x00, 0x00, 0x00,
    0x36, 0x43, 0x00, 0x20, 0x00, 0x00, 0x00,
    0x36, 0x43, 0x00, 0x20, 0x00, 0x00, 0x00,
]
METER_TOTAL_LEN = len(METER_HDR) + 5
METER_PAIR_RANGE = 0x400

ANIM_MAP = {
    0x00: "5A / light",
    0x01: "5B / medium",
    0x02: "5C / heavy",
    0x03: "2A / cr.L",
    0x04: "2B / cr.M",
    0x05: "2C / cr.H",
    0x06: "6C",
    0x08: "3C / alt",
    0x09: "j.A",
    0x0A: "j.B",
    0x0B: "j.C",
    0x0E: "6B",
    0x14: "donkey/dash-ish",
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


# ------------------------------------------------------------
def main():
    print("[scan] hooking dolphin…")
    try:
        hook()
    except Exception as e:
        print("[scan] hook FAILED:", e)
        sys.exit(1)
    print("[scan] hook OK")

    slots = read_slots()
    print("[scan] slots:")
    for s in slots:
        print("   ", s)

    print("[scan] reading MEM2…")
    mem = rbytes(MEM2_LO, MEM2_HI - MEM2_LO)
    print(f"[scan] MEM2 size = {len(mem)}")

    tail_offs = find_all_tails(mem)
    tail_offs.sort()
    print(f"[scan] found {len(tail_offs)} tail markers")

    clusters = cluster_tails(tail_offs)
    print(f"[scan] grouped into {len(clusters)} clusters")

    # your observed order
    cluster_to_slot = [0, 2, 1, 3]

    slot_normals = {i: [] for i in range(len(slots))}
    slot_specials = {i: [] for i in range(len(slots))}
    slot_supers = {i: [] for i in range(len(slots))}

    max_chars = min(4, len(clusters))

    for c_idx in range(max_chars):
        print(f"[scan] processing cluster {c_idx}")
        tails_in_cluster = clusters[c_idx]
        start_off = tails_in_cluster[0]
        if c_idx + 1 < len(clusters):
            end_off = clusters[c_idx + 1][0]
        else:
            end_off = min(len(mem), start_off + 0x8000)

        buf = mem[start_off:end_off]
        base_abs = MEM2_LO + start_off

        # pass 1: moves
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

        # pass 2: meter blocks
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

        # pass 3: assign defaults + override
        for mv in moves:
            aid = mv["id"]
            if mv["kind"] == "normal":
                mv["meter"] = DEFAULT_METER.get(aid)
            elif mv["kind"] == "special":
                mv["meter"] = SPECIAL_DEFAULT_METER
            else:
                mv["meter"] = None

            best_val = mv["meter"]
            best_dist = None
            mv_abs = mv["abs"]
            for m_abs, m_val in meters:
                if m_abs >= mv_abs and m_abs - mv_abs <= METER_PAIR_RANGE:
                    dist = m_abs - mv_abs
                    if best_dist is None or dist < best_dist:
                        best_dist = dist
                        best_val = m_val
            mv["meter"] = best_val

        # map to slot
        if c_idx < len(cluster_to_slot):
            slot_idx = cluster_to_slot[c_idx]
            for mv in moves:
                if mv["kind"] == "normal":
                    slot_normals[slot_idx].append(mv)
                elif mv["kind"] == "special":
                    slot_specials[slot_idx].append(mv)
                else:
                    slot_supers[slot_idx].append(mv)

    # print
    print("=== per-slot moves (with meter) ===")
    for idx, (slot_label, base, cid, cname) in enumerate(slots):
        print(f"{slot_label} ({cname})")
        normals_sorted = sorted(
            slot_normals[idx],
            key=lambda m: ((m["id"] if m["id"] is not None else 0xFF), m["abs"])
        )
        for mv in normals_sorted:
            aid = mv["id"]
            addr = mv["abs"]
            meter_val = mv.get("meter")
            name = ANIM_MAP.get(aid, f"anim_{(aid if aid is not None else 0xFF):02X}")
            if meter_val is not None:
                print(f"   {cname} {name:15s} @ 0x{addr:08X}  meter {meter_val:02X}")
            else:
                print(f"   {cname} {name:15s} @ 0x{addr:08X}")
        for mv in slot_specials[idx]:
            addr = mv["abs"]
            meter_val = mv.get("meter")
            if meter_val is not None:
                print(f"   {cname} SPECIAL?       @ 0x{addr:08X}  meter {meter_val:02X}")
            else:
                print(f"   {cname} SPECIAL?       @ 0x{addr:08X}")
        for mv in slot_supers[idx]:
            print(f"   {cname} SUPER?         @ 0x{mv['abs']:08X}")

    print("[scan] done.")


if __name__ == "__main__":
    main()
