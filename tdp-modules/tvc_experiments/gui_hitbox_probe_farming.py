# gui_hitbox_probe_farming.py
import struct
import json
import os
import tkinter as tk
from tkinter import ttk

print("[gui] starting…")

HAVE_DOLPHIN = True
try:
    from dolphin_io import hook, rbytes, rd32
    import dolphin_memory_engine as dme
    from constants import SLOTS, MEM2_LO, MEM2_HI, CHAR_NAMES
    print("[gui] dolphin modules imported")
except Exception as e:
    print("[gui] dolphin import FAILED:", e)
    HAVE_DOLPHIN = False
    SLOTS = []
    MEM2_LO = 0x90000000
    MEM2_HI = 0x94000000
    CHAR_NAMES = {}

TAIL = b"\x00\x00\x00\x38\x01\x33\x00\x00"
CLUSTER_GAP = 0x4000
RYU_ID = 12

# these are YOUR ryu field offsets from the tail for this layout
RYU_FIELDS = [
    ("5A",      -0x10),
    ("5B",       0x420),
    ("5C",       0x834),
    ("Tatsu L",  0x59B0),
]

FARM_FILE = "ryu_tails.json"

def load_farmed_tails():
    if not os.path.exists(FARM_FILE):
        return {}
    try:
        with open(FARM_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_farmed_tails(data):
    try:
        with open(FARM_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print("[gui] failed to save farm file:", e)

def write_bytes(addr: int, data: bytes):
    if not HAVE_DOLPHIN:
        return
    dme.write_bytes(addr, data)

def read_mem2() -> bytes:
    if not HAVE_DOLPHIN:
        return b""
    return rbytes(MEM2_LO, MEM2_HI - MEM2_LO)

def find_tail_clusters(mem: bytes):
    hits = []
    off = 0
    while True:
        i = mem.find(TAIL, off)
        if i == -1:
            break
        hits.append(i)
        off = i + 1
    if not hits:
        return []
    clusters = []
    cur = [hits[0]]
    for h in hits[1:]:
        if h - cur[-1] <= CLUSTER_GAP:
            cur.append(h)
        else:
            clusters.append(cur)
            cur = [h]
    clusters.append(cur)
    return clusters

def read_slot_chars():
    info = []
    for slotname, ptr_addr, teamtag in SLOTS:
        base = 0
        cid = None
        name = "—"
        if HAVE_DOLPHIN:
            try:
                base = rd32(ptr_addr)
            except Exception:
                base = 0
            if base:
                try:
                    cid = rd32(base + 0x14)
                except Exception:
                    cid = None
        if cid is not None:
            name = CHAR_NAMES.get(cid, f"ID_{cid}")
        info.append((slotname, base, cid, name))
    return info

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TvC Ryu slot farmer")
        self.geometry("680x460")

        self.tree = ttk.Treeview(
            self,
            columns=("slot","char","cluster","base","farmed"),
            show="headings",
            height=6
        )
        for c in ("slot","char","cluster","base","farmed"):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=120, stretch=True)
        self.tree.pack(fill=tk.BOTH, expand=True)

        self.ryu_frame = ttk.LabelFrame(self, text="Ryu fields (2-byte, carry)")
        self.ryu_frame.pack(fill=tk.X, pady=4)

        self.ryu_rows = []
        for name, off in RYU_FIELDS:
            row = ttk.Frame(self.ryu_frame); row.pack(fill=tk.X, pady=1)
            ttk.Label(row, text=name, width=12).pack(side=tk.LEFT)
            addr_var = tk.StringVar(value="—")
            val_var = tk.StringVar(value="—")
            ttk.Label(row, textvariable=addr_var, width=18).pack(side=tk.LEFT)
            ttk.Label(row, textvariable=val_var, width=12).pack(side=tk.LEFT)

            def make_bump(delta, addr_var=addr_var, val_var=val_var):
                def _do():
                    if not HAVE_DOLPHIN:
                        return
                    txt = addr_var.get()
                    if txt == "—":
                        return
                    addr = int(txt, 16)
                    raw = rbytes(addr, 2)
                    if not raw or len(raw) != 2:
                        return
                    hi, lo = raw[0], raw[1]
                    value = (hi << 8) | lo
                    step = int(delta * 255)
                    value = (value + step) & 0xFFFF
                    new_hi = (value >> 8) & 0xFF
                    new_lo = value & 0xFF
                    write_bytes(addr, bytes([new_hi, new_lo]))
                    val_var.set(f"{new_hi:02X} {new_lo:02X}")
                return _do

            ttk.Button(row, text="+0.10", command=make_bump(0.10)).pack(side=tk.LEFT, padx=2)
            ttk.Button(row, text="-0.10", command=make_bump(-0.10)).pack(side=tk.LEFT, padx=2)

            self.ryu_rows.append((name, off, addr_var, val_var))

        # frozen clusters
        self.saved_clusters = None
        self.farmed = load_farmed_tails()  # {"P1-C1": 0x908AF014, ...}

        self.after(400, self.update_data)

    def update_data(self):
        # clear tree
        for iid in self.tree.get_children():
            self.tree.delete(iid)

        slots = read_slot_chars()
        mem = read_mem2() if HAVE_DOLPHIN else b""

        if self.saved_clusters is None and mem:
            clusters = find_tail_clusters(mem)
            self.saved_clusters = clusters
            print(f"[gui] found {len(clusters)} clusters, frozen")
        else:
            clusters = self.saved_clusters

        # map slot -> tail (from current mem)
        slot_to_tail = {}
        for idx, (slotname, base, cid, name) in enumerate(slots):
            tail_addr = None
            if clusters and idx < len(clusters):
                mem_off = clusters[idx][0]
                tail_addr = MEM2_LO + mem_off
            slot_to_tail[slotname] = tail_addr

            already = self.farmed.get(slotname)
            self.tree.insert(
                "",
                tk.END,
                values=(
                    slotname,
                    name,
                    f"0x{tail_addr:08X}" if tail_addr else "—",
                    f"0x{base:08X}" if base else "—",
                    f"0x{already:08X}" if already else "—",
                ),
            )

        # if we see Ryu in a slot, farm that tail
        for slotname, base, cid, name in slots:
            if cid == RYU_ID:
                current_tail = slot_to_tail.get(slotname)
                if current_tail:
                    # store it if new
                    if self.farmed.get(slotname) != current_tail:
                        self.farmed[slotname] = current_tail
                        save_farmed_tails(self.farmed)
                        print(f"[gui] farmed tail for {slotname}: 0x{current_tail:08X}")
                ryu_tail = current_tail
                break
        else:
            ryu_tail = None

        # update Ryu fields from either current tail or farmed tail
        for name, off, addr_var, val_var in self.ryu_rows:
            if ryu_tail:
                addr = ryu_tail + off
                addr_var.set(f"0x{addr:08X}")
                raw = rbytes(addr, 2)
                if raw and len(raw) == 2:
                    val_var.set(f"{raw[0]:02X} {raw[1]:02X}")
                else:
                    val_var.set("ERR")
            else:
                addr_var.set("—")
                val_var.set("—")

        self.after(500, self.update_data)


if __name__ == "__main__":
    if HAVE_DOLPHIN:
        print("[gui] hooking dolphin…")
        try:
            hook()
            print("[gui] hooked.")
        except Exception as e:
            print("[gui] hook FAILED:", e)
    else:
        print("[gui] running without dolphin")

    app = App()
    app.mainloop()
