# tvc_move_viewer.py
#
# GUI viewer for TvC move tables
# now with:
# - meter
# - animation speed
# - active frames
# - attack damage

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

# meter block
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
# 35 10 20 3F 00 XX XX XX 00 00 00 3F 00 00 00 YY
DAMAGE_HDR = [
    0x35, 0x10, 0x20, 0x3F, 0x00,
]
DAMAGE_TOTAL_LEN = 16
DAMAGE_PAIR_RANGE = 0x400

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
    """
    35 10 20 3F 00 XX XX XX 00 00 00 3F 00 00 00 YY
    return (damage_int, damage_flag)
    """
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


# ------------------------------------------------------------
def scan_once():
    slots_info = read_slots()
    mem = rbytes(MEM2_LO, MEM2_HI - MEM2_LO)
    tails = find_all_tails(mem)
    tails.sort()
    clusters = cluster_tails(tails)

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

        # PASS 6: attach to moves
        for mv in moves:
            aid = mv["id"]
            # meter
            if mv["kind"] == "normal":
                mv["meter"] = DEFAULT_METER.get(aid)
            elif mv["kind"] == "special":
                mv["meter"] = SPECIAL_DEFAULT_METER
            else:
                mv["meter"] = None

            # override meter if near
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

            # anim speed
            if aid is not None and aid in speed_map:
                mv["speed"] = speed_map[aid]
            else:
                mv["speed"] = None

            # active frames
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

        # assign into result
        slot_idx = cluster_to_slot[c_idx] if c_idx < len(cluster_to_slot) else c_idx
        slot_label, base_ptr, cid, cname = slots_info[slot_idx] if slot_idx < len(slots_info) else (f"slot{slot_idx}", 0, None, "—")
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
        self.geometry("1100x540")

        left = ttk.Frame(self); left.pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=4)
        ttk.Label(left, text="Characters").pack(anchor="w")

        self.slot_list = tk.Listbox(left, height=6)
        self.slot_list.pack(fill=tk.Y, expand=False)
        self.slot_list.bind("<<ListboxSelect>>", self.on_slot_sel)

        self.refresh_btn = ttk.Button(left, text="Refresh", command=self.refresh_data)
        self.refresh_btn.pack(pady=6)

        right = ttk.Frame(self); right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=4, pady=4)

        cols = ("move", "addr", "meter", "speed", "active", "damage", "dflag")
        self.tree = ttk.Treeview(right, columns=cols, show="headings")
        self.tree.heading("move", text="Move")
        self.tree.heading("addr", text="Addr")
        self.tree.heading("meter", text="Meter")
        self.tree.heading("speed", text="Speed")
        self.tree.heading("active", text="Active (start-end)")
        self.tree.heading("damage", text="Damage")
        self.tree.heading("dflag", text="DmgFlag")

        self.tree.column("move", width=200)
        self.tree.column("addr", width=120)
        self.tree.column("meter", width=60)
        self.tree.column("speed", width=60)
        self.tree.column("active", width=120)
        self.tree.column("damage", width=90)
        self.tree.column("dflag", width=70)

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
        # which slot did the user click?
        sel = self.slot_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self.slots_data):
            return

        slot = self.slots_data[idx]
        moves = slot.get("moves", [])

        # clear the table
        for iid in self.tree.get_children():
            self.tree.delete(iid)

        # sort by anim id, then by address
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

            # move name
            if aid is not None:
                name = ANIM_MAP.get(aid, f"anim_{aid:02X}")
            else:
                # distinguish supers vs specials
                if mv.get("kind") == "super":
                    name = "SUPER?"
                else:
                    name = "SPECIAL?"

            # address
            addr_txt = f"0x{addr:08X}"

            # meter in hex like before
            meter_txt = f"{meter:02X}" if meter is not None else ""

            # animation speed
            speed_txt = f"{speed:02X}" if speed is not None else ""

            # active frames
            if a_s is not None and a_e is not None:
                active_txt = f"{a_s}-{a_e}"
            else:
                active_txt = ""

            # damage: show as decimal, human readable
            if dmg is not None:
                # dmg is already an int we built from 3 bytes
                dmg_txt = f"{dmg:,d}"   # e.g. 2,720
            else:
                dmg_txt = ""

            # damage flag stays hex so you can see 04 / 08 / 0C
            dflag_txt = f"{dflag:02X}" if dflag is not None else ""

            # insert row
            self.tree.insert(
                "",
                tk.END,
                values=(name, addr_txt, meter_txt, speed_txt, active_txt, dmg_txt, dflag_txt),
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
