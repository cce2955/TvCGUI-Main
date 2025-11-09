# scan_normals_specials_by_slot.py
#
# - find clusters (4 chars) in MEM2
# - map clusters -> slots as (0->P1C1, 1->P2C1, 2->P1C2, 3->P2C2)
# - within each cluster:
#     * collect normals (have 01 ?? 01 3C)
#     * collect special-like (have anim header but NO 01 ?? 01 3C)
#     * collect super-like (match the "attack end" style header you pasted)


from dolphin_io import hook, rbytes, rd32
from constants import MEM2_LO, MEM2_HI, SLOTS, CHAR_NAMES

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

# "attack end" / end-of-action block you pasted (we'll match only prefix)
SUPER_END_HDR = [
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x12, 0x18, 0x3F,
]

ANIM_ID_PATTERN = [0x01, None, 0x01, 0x3C]
LOOKAHEAD_AFTER_HDR = 0x80

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


def main():
    hook()
    slots = read_slots()
    mem = rbytes(MEM2_LO, MEM2_HI - MEM2_LO)

    tail_offs = find_all_tails(mem)
    tail_offs.sort()
    clusters = cluster_tails(tail_offs)

    # we saw: cluster0=P1C1, cluster1=P2C1, cluster2=P1C2, cluster3=P2C2
    cluster_to_slot = [0, 2, 1, 3]

    slot_normals = {i: [] for i in range(len(slots))}
    slot_specials = {i: [] for i in range(len(slots))}
    slot_supers = {i: [] for i in range(len(slots))}

    max_chars = min(4, len(clusters))

    for c_idx in range(max_chars):
        tails_in_cluster = clusters[c_idx]
        start_off = tails_in_cluster[0]
        if c_idx + 1 < len(clusters):
            end_off = clusters[c_idx + 1][0]
        else:
            end_off = min(len(mem), start_off + 0x8000)

        buf = mem[start_off:end_off]
        base_abs = MEM2_LO + start_off

        normals_here = []
        specials_here = []
        supers_here = []

        i = 0
        while i < len(buf):
            # supers (simple: match the start)
            if match_bytes(buf, i, SUPER_END_HDR):
                supers_here.append(base_abs + i)
                i += len(SUPER_END_HDR)
                continue

            # AIR
            if match_bytes(buf, i, AIR_HDR):
                search_start = i + AIR_HDR_LEN
                search_end = min(search_start + LOOKAHEAD_AFTER_HDR, len(buf))
                found_normal = False
                for p in range(search_start, search_end):
                    if match_bytes(buf, p, ANIM_HDR):
                        anim_id = get_anim_id_after_hdr(buf, p)
                        if anim_id is not None and anim_id in NORMAL_IDS:
                            normals_here.append((anim_id, base_abs + p))
                        else:
                            specials_here.append(base_abs + p)
                        found_normal = True
                        break
                i += AIR_HDR_LEN
                continue

            # CMD
            if match_bytes(buf, i, CMD_HDR):
                search_start = i + CMD_HDR_LEN + 3
                search_end = min(search_start + LOOKAHEAD_AFTER_HDR, len(buf))
                found_normal = False
                for p in range(search_start, search_end):
                    if match_bytes(buf, p, ANIM_HDR):
                        anim_id = get_anim_id_after_hdr(buf, p)
                        if anim_id is not None and anim_id in NORMAL_IDS:
                            normals_here.append((anim_id, base_abs + p))
                        else:
                            specials_here.append(base_abs + p)
                        found_normal = True
                        break
                i += CMD_HDR_LEN
                continue

            # PLAIN
            if match_bytes(buf, i, ANIM_HDR):
                anim_id = get_anim_id_after_hdr(buf, i)
                if anim_id is not None and anim_id in NORMAL_IDS:
                    normals_here.append((anim_id, base_abs + i))
                else:
                    specials_here.append(base_abs + i)
                i += len(ANIM_HDR)
                continue

            i += 1

        # map cluster → slot
        if c_idx < len(cluster_to_slot):
            slot_idx = cluster_to_slot[c_idx]
            slot_normals[slot_idx].extend(normals_here)
            slot_specials[slot_idx].extend(specials_here)
            slot_supers[slot_idx].extend(supers_here)

    # print
    print("=== per-slot moves ===")
    for idx, (slot_label, base, cid, cname) in enumerate(slots):
        print(f"{slot_label} ({cname})")
        # normals
        for anim_id, addr in sorted(slot_normals[idx], key=lambda x: x[0]):
            name = ANIM_MAP.get(anim_id, f"anim_{anim_id:02X}")
            print(f"   {cname} {name:15s} @ 0x{addr:08X}")
        # specials
        for addr in slot_specials[idx]:
            print(f"   {cname} SPECIAL?       @ 0x{addr:08X}")
        # supers
        for addr in slot_supers[idx]:
            print(f"   {cname} SUPER?         @ 0x{addr:08X}")


if __name__ == "__main__":
    main()
