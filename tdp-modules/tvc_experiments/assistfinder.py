#!/usr/bin/env python3
#
# Assist Script Navigator / Decoder
#
# - Scans MEM2 for an anchor pattern (default 01 A8 01 3C).
# - Finds script start/end around each anchor.
# - Classifies each block by slot/char based on SLOTS pointers.
# - Shows blocks in a table.
# - Double-click a row to open a decoded tree view of that script.
#   Inside the script:
#       * First 01 ?? 01 3C  -> Owner anim (assist pose)
#       * First 34 TT 20 00  -> Primary spawn (projectile) after owner anim
#

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import binascii

from dolphin_io import hook, rbytes, rd32
from constants import MEM2_LO, MEM2_HI, SLOTS, CHAR_NAMES

# ============================================================
# Configurable Parameters
# ============================================================

# NOTE: these were 0x200 / 0x300 before. That was too small for Ryu’s assist,
# where the real “owner” anim (01 0C 01 3C) sits ~0x31A bytes BEFORE 01 A8 01 3C.
# Bump them so the block is big enough to include the real start + early anims.
DEFAULT_ANCHOR = b"\x01\xA8\x01\x3C"     # assist taunt ID
MAX_BACKSCAN   = 0x1000                 # how far to walk upward from anchor
MAX_FORWARD    = 0x1000                 # max script block size we capture


# ============================================================
# Helper: Convert "01 A8 01 3C" → bytes
# ============================================================

def hex_to_bytes(s: str) -> bytes:
    s = s.strip().replace(" ", "")
    if len(s) % 2 != 0:
        raise ValueError("Hex string length must be even.")
    try:
        return binascii.unhexlify(s)
    except Exception:
        raise ValueError("Invalid hex string.")


# ============================================================
# Slot ranges (classify addresses -> P1-C1, etc)
# ============================================================

def build_slot_ranges():
    """
    Build slot ranges using SLOTS definitions.
    Returns list of (slot_label, char_name, start_addr, end_addr).
    """
    ranges = []
    for slot_label, ptr_addr, team_tag in SLOTS:
        base = rd32(ptr_addr)
        if not base:
            ranges.append((slot_label, "—", 0, 0))
            continue

        char_id = rd32(base + 0x14)
        char_name = CHAR_NAMES.get(char_id, f"ID_{char_id}")

        start_addr = base
        end_addr   = base + 0x50000  # generous window per slot
        ranges.append((slot_label, char_name, start_addr, end_addr))
    return ranges


def classify_address(addr: int, slot_ranges):
    for slot_label, char_name, a0, a1 in slot_ranges:
        if a0 <= addr <= a1:
            return slot_label, char_name
    return "?", "?"


# ============================================================
# Script boundary detection
# ============================================================

START_OPCODES = {0x33, 0x34, 0x41}
HITBOX_OPCODE = 0x04  # with 0x0C after it in practice


def find_script_start(mem: bytes, anchor_off: int):
    """
    Walk BACKWARD from anchor until a plausible script start.
    With MAX_BACKSCAN bumped to 0x1000, this now sees the earlier
    movement / hitbox / 33/34/41 blocks for big assists like Ryu.
    """
    for back in range(1, MAX_BACKSCAN):
        pos = anchor_off - back
        if pos < 0:
            break

        b0 = mem[pos]

        if b0 in START_OPCODES:
            return pos

        if b0 == HITBOX_OPCODE and pos + 1 < len(mem) and mem[pos+1] == 0x0C:
            return pos

    return None


def find_script_end(mem: bytes, start_off: int):
    """
    Walk forward up to MAX_FORWARD bytes.
    """
    end_off = start_off + MAX_FORWARD
    if end_off > len(mem):
        end_off = len(mem)
    return end_off


# ============================================================
# Script decoder
# ============================================================
class ScriptDecoder:
    """
    Decode a script block into logical ops.

    Each op:
        {
            "type": "anim"/"hitbox"/"spawn"/"call"/"move"/"meter"/"unknown",
            "addr": absolute_address,
            "off": offset_from_block_start,
            "raw": [byte,...],
            "desc": "text",
            "role": optional "owner_anim" / "primary_spawn"
        }
    """

    def __init__(self, data: bytes, base_addr: int, anchor_rel: int):
        # data is the entire script block [start_addr .. end_addr)
        self.mem = data
        self.base = base_addr          # script_start absolute
        self.start_off = 0
        self.end_off = len(data)
        # clamp anchor_rel into range
        self.anchor_rel = max(0, min(anchor_rel, self.end_off - 1))

    # ---------------------------------------------
    # Pre-pass: find owner anim and primary spawn
    # ---------------------------------------------

    def _find_owner_anim_offset(self):
        """
        Walk BACKWARDS from anchor_rel to find nearest 01 XX 01 3C.
        This is the 'owner pose' animation for the assist.
        With the larger script window this will now hit 01 0C 01 3C
        for Ryu instead of the later 01 35 01 3C.
        """
        for pos in range(self.anchor_rel, -1, -1):
            if self.mem[pos] == 0x01 and pos + 3 < self.end_off:
                if self.mem[pos + 2] == 0x01 and self.mem[pos + 3] == 0x3C:
                    return pos
        return None

    def _find_primary_spawn_offset(self, owner_off):
        """
        Walk FORWARD starting at owner_off (if present) or anchor_rel
        to find the first 34 TT 20 00 → primary projectile spawn.
        """
        start = owner_off if owner_off is not None else self.anchor_rel
        for pos in range(start, self.end_off - 3):
            if (
                self.mem[pos] == 0x34
                and self.mem[pos + 2] == 0x20
                and self.mem[pos + 3] == 0x00
            ):
                return pos
        return None

    # ---------------------------------------------
    # Main decode
    # ---------------------------------------------

    def decode(self):
        owner_off = self._find_owner_anim_offset()
        primary_spawn_off = self._find_primary_spawn_offset(owner_off)

        ops = []
        off = self.start_off

        while off < self.end_off:
            b0 = self.mem[off]

            # 01 XX 01 3C — animation call
            if b0 == 0x01 and off + 3 < self.end_off:
                if self.mem[off + 2] == 0x01 and self.mem[off + 3] == 0x3C:
                    anim_id = (self.mem[off] << 8) | self.mem[off + 1]  # 0x01XX
                    role = None
                    desc = f"Play animation 0x{anim_id:04X}"

                    if owner_off is not None and off == owner_off:
                        role = "owner_anim"
                        desc = f"Owner anim (assist pose) 0x{anim_id:04X}"

                    ops.append({
                        "type": "anim",
                        "id": anim_id,
                        "addr": self.base + off,
                        "off": off,
                        "raw": self._grab(off, 4),
                        "desc": desc,
                        "role": role,
                    })
                    off += 4
                    continue

            # 04 0C HH 3F — hitbox create
            if b0 == 0x04 and off + 3 < self.end_off:
                if self.mem[off + 1] == 0x0C and self.mem[off + 3] == 0x3F:
                    hb_id = self.mem[off + 2]
                    ops.append({
                        "type": "hitbox",
                        "id": hb_id,
                        "addr": self.base + off,
                        "off": off,
                        "raw": self._grab(off, 4),
                        "desc": f"Hitbox create (ID {hb_id})",
                        "role": None,
                    })
                    off += 4
                    continue

            # 34 TT 20 00 — projectile/actor spawn
            if b0 == 0x34 and off + 3 < self.end_off:
                TT = self.mem[off + 1]
                if self.mem[off + 2] == 0x20 and self.mem[off + 3] == 0x00:
                    role = None
                    desc = f"Spawn actor template 0x{TT:02X}"

                    if primary_spawn_off is not None and off == primary_spawn_off:
                        role = "primary_spawn"
                        desc = f"Primary spawn (projectile) template 0x{TT:02X}"

                    ops.append({
                        "type": "spawn",
                        "actor": TT,
                        "addr": self.base + off,
                        "off": off,
                        "raw": self._grab(off, 4),
                        "desc": desc,
                        "role": role,
                    })
                    off += 4
                    continue

            # 41 AA BB CC — subroutine call
            if b0 == 0x41 and off + 3 < self.end_off:
                AA = self.mem[off + 1]
                BB = self.mem[off + 2]
                CC = self.mem[off + 3]
                tgt = (CC << 16) | (BB << 8) | AA
                ops.append({
                    "type": "call",
                    "target": tgt,
                    "addr": self.base + off,
                    "off": off,
                    "raw": self._grab(off, 4),
                    "desc": f"Call subroutine 0x{tgt:06X}",
                    "role": None,
                })
                off += 4
                continue

            # 33 ?? ?? 20 3F — movement / velocity block
            if b0 == 0x33 and off + 4 < self.end_off:
                if self.mem[off + 3] == 0x20 and self.mem[off + 4] == 0x3F:
                    raw = self._grab(off, 5)
                    ops.append({
                        "type": "move",
                        "addr": self.base + off,
                        "off": off,
                        "raw": raw,
                        "desc": "Movement / velocity block",
                        "role": None,
                    })
                    off += 5
                    continue

            # 36 XX .. .. .. .. .. .. .. .. .. .. — meter / resource op (12-byte blob)
            #
            # Example you gave:
            #   36 43 00 20 00 00 00 C8 00 00 00 04
            # Parsed here as:
            #   sub   = 0x43
            #   p1    = 0x00200000
            #   p2    = 0x000000C8
            #   flags = 0x0004
            #
            # Semantics are still WIP, but this at least groups them so you can see
            # every "meter op" in the decoded tree instead of as raw unknown bytes.
            if b0 == 0x36 and off + 11 < self.end_off:
                sub = self.mem[off + 1]

                p1 = (
                    (self.mem[off + 2] << 24)
                    | (self.mem[off + 3] << 16)
                    | (self.mem[off + 4] << 8)
                    | self.mem[off + 5]
                )
                p2 = (
                    (self.mem[off + 6] << 24)
                    | (self.mem[off + 7] << 16)
                    | (self.mem[off + 8] << 8)
                    | self.mem[off + 9]
                )
                flags = (self.mem[off + 10] << 8) | self.mem[off + 11]

                desc = (
                    f"Meter op (sub=0x{sub:02X}, "
                    f"p1=0x{p1:08X}, p2=0x{p2:08X}, flags=0x{flags:04X})"
                )

                ops.append({
                    "type": "meter",
                    "sub": sub,
                    "p1": p1,
                    "p2": p2,
                    "flags": flags,
                    "addr": self.base + off,
                    "off": off,
                    "raw": self._grab(off, 12),
                    "desc": desc,
                    "role": None,
                })
                off += 12
                continue

            # fallback: unknown byte
            ops.append({
                "type": "unknown",
                "addr": self.base + off,
                "off": off,
                "raw": [self.mem[off]],
                "desc": f"Unknown opcode 0x{self.mem[off]:02X}",
                "role": None,
            })
            off += 1

        return ops

    def _grab(self, off, length):
        return [self.mem[off + i] for i in range(length)]

# ============================================================
# Decoded tree + hex viewer helpers
# ============================================================

def populate_decoded_tree(tree_widget: ttk.Treeview, ops):
    tree_widget.delete(*tree_widget.get_children())

    root = tree_widget.insert("", "end", text="Script Block", values=("Script Block",))

    # Main buckets
    cats = {
        "owner_anim": tree_widget.insert(root, "end", text="Owner Anim (Assist Pose)", values=()),
        "primary_spawn": tree_widget.insert(root, "end", text="Primary Spawn (Projectile)", values=()),
        "anim": tree_widget.insert(root, "end", text="Other Animations", values=()),
        "hitbox": tree_widget.insert(root, "end", text="Hitboxes", values=()),
        "spawn": tree_widget.insert(root, "end", text="Other Spawns", values=()),
        "call": tree_widget.insert(root, "end", text="Subroutine Calls", values=()),
        "move": tree_widget.insert(root, "end", text="Movement Blocks", values=()),
        "meter": tree_widget.insert(root, "end", text="Meter / Resource Ops", values=()),
        "unknown": tree_widget.insert(root, "end", text="Unknown Opcodes", values=()),
    }

    for op in ops:
        # Decide which bucket to drop into
        role = op.get("role")
        if role == "owner_anim":
            parent = cats["owner_anim"]
        elif role == "primary_spawn":
            parent = cats["primary_spawn"]
        else:
            parent = cats.get(op["type"], root)

        raw_hex = " ".join(f"{b:02X}" for b in op["raw"])
        tree_widget.insert(
            parent,
            "end",
            text=op["desc"],
            values=(f"0x{op['addr']:08X}", op["off"], raw_hex),
        )

    for node in cats.values():
        tree_widget.item(node, open=True)
    tree_widget.item(root, open=True)


def open_hex_viewer(addr: int, parent):
    win = tk.Toplevel(parent)
    win.title(f"Hex @ 0x{addr:08X}")

    txt = tk.Text(win, width=80, height=25)
    txt.pack(fill="both", expand=True)

    data = rbytes(addr, 0x100)
    if not data:
        txt.insert("end", "Failed to read memory.\n")
        return

    for i in range(0, 0x100, 16):
        row_addr = addr + i
        chunk = data[i:i+16]
        hex_str = " ".join(f"{b:02X}" for b in chunk)
        txt.insert("end", f"{row_addr:08X}  {hex_str}\n")

    txt.config(state="disabled")


def setup_decoded_tree_context_menu(tree: ttk.Treeview, parent):
    menu = tk.Menu(tree, tearoff=0)

    def copy_addr():
        item = tree.focus()
        if not item:
            return
        vals = tree.item(item, "values")
        if not vals:
            return
        addr = vals[0]
        parent.clipboard_clear()
        parent.clipboard_append(addr)

    def hex_view():
        item = tree.focus()
        if not item:
            return
        vals = tree.item(item, "values")
        if not vals:
            return
        try:
            addr = int(vals[0], 16)
        except Exception:
            return
        open_hex_viewer(addr, parent)

    menu.add_command(label="Copy Address", command=copy_addr)
    menu.add_command(label="Hex View", command=hex_view)

    def on_rclick(event):
        iid = tree.identify_row(event.y)
        if iid:
            tree.selection_set(iid)
            menu.tk_popup(event.x_root, event.y_root)

    tree.bind("<Button-3>", on_rclick)


# ============================================================
# Main GUI
# ============================================================

class AssistScriptNavigatorGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Assist Script Navigator / Decoder")

        self.anchor_hex_var = tk.StringVar(value="01 A8 01 3C")

        self._build_gui()
        self._start_hook_thread()

    def _build_gui(self):
        top = tk.Frame(self.root)
        top.pack(side="top", fill="x", padx=6, pady=6)

        tk.Label(top, text="Anchor pattern (hex):").pack(side="left")
        tk.Entry(top, textvariable=self.anchor_hex_var, width=18).pack(side="left", padx=4)
        tk.Button(top, text="Scan MEM2", command=self.on_scan).pack(side="left", padx=10)

        self.status = tk.Label(top, text="Hooking Dolphin…")
        self.status.pack(side="left", padx=12)

        cols = ("slot", "char", "anchor", "start", "end", "len")
        self.tree = ttk.Treeview(self.root, columns=cols, show="headings")
        for col, lbl in zip(cols,
                            ["Slot", "Char", "Anchor Addr", "Script Start", "Script End", "Bytes"]):
            self.tree.heading(col, text=lbl)
            self.tree.column(col, width=130)
        self.tree.pack(fill="both", expand=True, padx=6, pady=4)

        # interactions
        self.tree.bind("<Double-1>", self.on_tree_double_click)
        self.tree.bind("<Button-3>", self.on_tree_right_click)

        # context menu for main list
        self.main_menu = tk.Menu(self.root, tearoff=0)
        self.main_menu.add_command(label="Copy Anchor Addr", command=self.copy_anchor_addr)
        self.main_menu.add_command(label="Copy Script Start Addr", command=self.copy_start_addr)
        self.main_menu.add_command(label="Copy Script End Addr", command=self.copy_end_addr)

    # hook Dolphin in a background thread
    def _start_hook_thread(self):
        t = threading.Thread(target=self._hook_thread, daemon=True)
        t.start()

    def _hook_thread(self):
        hook()
        self.status.config(text="Dolphin connected.")

    # --------------------------------------------------------
    # Scanning
    # --------------------------------------------------------

    def on_scan(self):
        self.tree.delete(*self.tree.get_children())
        self.status.config(text="Scanning MEM2…")
        t = threading.Thread(target=self._scan_thread, daemon=True)
        t.start()

    def _scan_thread(self):
        mem = rbytes(MEM2_LO, MEM2_HI - MEM2_LO)
        if not mem:
            self.status.config(text="Failed to read MEM2.")
            return

        try:
            anchor_pat = hex_to_bytes(self.anchor_hex_var.get())
        except Exception as e:
            self.status.config(text=str(e))
            return

        anchors = []
        start = 0
        while True:
            idx = mem.find(anchor_pat, start)
            if idx == -1:
                break
            anchors.append(idx)
            start = idx + 1

        slot_ranges = build_slot_ranges()
        rows = []

        for off in anchors:
            anchor_abs = MEM2_LO + off

            s_off = find_script_start(mem, off)
            if s_off is None:
                continue
            e_off = find_script_end(mem, s_off)

            script_start = MEM2_LO + s_off
            script_end   = MEM2_LO + e_off

            slot_label, char_name = classify_address(anchor_abs, slot_ranges)

            rows.append({
                "slot": slot_label,
                "char": char_name,
                "anchor": anchor_abs,
                "start": script_start,
                "end": script_end,
                "length": script_end - script_start,
            })

        self.root.after(0, lambda: self._populate_tree(rows))

    def _populate_tree(self, rows):
        for r in rows:
            self.tree.insert(
                "",
                "end",
                values=(
                    r["slot"],
                    r["char"],
                    f"0x{r['anchor']:08X}",
                    f"0x{r['start']:08X}",
                    f"0x{r['end']:08X}",
                    r["length"],
                ),
            )
        self.status.config(text=f"Found {len(rows)} script blocks.")

    # --------------------------------------------------------
    # Main list interactions
    # --------------------------------------------------------

    def on_tree_double_click(self, event):
        item = self.tree.identify_row(event.y)
        if not item:
            return

        self.tree.selection_set(item)
        vals = self.tree.item(item, "values")
        if not vals or len(vals) < 6:
            return

        try:
            anchor_addr = int(vals[2], 16)  # Anchor Addr col
            start_addr  = int(vals[3], 16)  # Script Start
            end_addr    = int(vals[4], 16)  # Script End
        except Exception:
            return

        length = end_addr - start_addr
        if length <= 0:
            return

        anchor_rel = anchor_addr - start_addr
        if anchor_rel < 0:
            anchor_rel = 0

        data = rbytes(start_addr, length)
        if not data:
            messagebox.showerror("Error", "Failed to read script block from Dolphin.")
            return

        decoder = ScriptDecoder(data, start_addr, anchor_rel)
        ops = decoder.decode()

        win = tk.Toplevel(self.root)
        win.title(f"Decoded Script @ 0x{start_addr:08X}")

        cols = ("addr", "off", "raw")
        tree = ttk.Treeview(win, columns=cols, show="tree headings")
        tree.heading("addr", text="Address")
        tree.heading("off", text="Offset")
        tree.heading("raw", text="Raw Bytes")
        tree.column("addr", width=140)
        tree.column("off", width=60)
        tree.column("raw", width=260)
        tree.pack(fill="both", expand=True)

        populate_decoded_tree(tree, ops)
        setup_decoded_tree_context_menu(tree, win)

    def on_tree_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            self.main_menu.tk_popup(event.x_root, event.y_root)

    def _get_selected_row_vals(self):
        item = self.tree.focus()
        if not item:
            return None
        return self.tree.item(item, "values")

    def copy_anchor_addr(self):
        vals = self._get_selected_row_vals()
        if not vals:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(vals[2])

    def copy_start_addr(self):
        vals = self._get_selected_row_vals()
        if not vals:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(vals[3])

    def copy_end_addr(self):
        vals = self._get_selected_row_vals()
        if not vals:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(vals[4])


# ============================================================
# Main Entry
# ============================================================

if __name__ == "__main__":
    root = tk.Tk()
    app = AssistScriptNavigatorGUI(root)
    root.mainloop()
