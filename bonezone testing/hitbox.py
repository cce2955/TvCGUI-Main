"""
╔══════════════════════════════════════════════════════════════╗
║         TvC Vertex Reader  ,  hitbox from geometry           ║
╚══════════════════════════════════════════════════════════════╝

WHAT THIS DOES:
  Scans memory near the draw command table (0x924707C0) for the
  actual vertex position buffer. Once found, reads XYZ world-space
  floats every frame and computes bounding boxes per body region.

DRAW COMMAND TABLE: 0x924707C0
  Each 16-byte row = one draw call
  col[7] (last uint16) = byte offset into vertex buffer, stride=0xD0
  First offset seen: 0x1BC8 → vertex buffer base is somewhere below that

VERTEX FORMAT (Wii GX standard):
  +0x00  float X   world space
  +0x04  float Y   world space  
  +0x08  float Z   world space
  +0x0C  ... normals, UVs, color follow

Requires: dolphin_memory_engine
"""

import struct, sys, time, os
import dolphin_memory_engine as dme

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════

DRAW_CMD_TABLE   = 0x924707C0   # confirmed draw command table start
DRAW_CMD_STRIDE  = 0x10         # 16 bytes per draw command row
VERTEX_STRIDE    = 0xD0         # 208 bytes per vertex record (from offset deltas)
FIRST_VTX_OFFSET = 0x1BC8       # offset of first vertex in vertex buffer

# Search range for vertex buffer base: scan backwards from draw table
# The buffer is likely within 0x10000 bytes before the draw table
SCAN_BACK  = 0x8000
SCAN_FWD   = 0x1000

# Valid world-space float range for a fighting game character
# Ryu is roughly 1.8m tall; game units appear to be ~1 unit = 1m
WORLD_MIN  = -50.0
WORLD_MAX  =  50.0

# How many vertex records to read per frame for bounding box
MAX_VERTICES = 512

# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def rf32(addr):
    raw = dme.read_bytes(addr, 4)
    return struct.unpack('>f', raw)[0]

def ru32(addr):
    raw = dme.read_bytes(addr, 4)
    return struct.unpack('>I', raw)[0]

def ru16(addr):
    raw = dme.read_bytes(addr, 2)
    return struct.unpack('>H', raw)[0]

def is_world_float(f):
    return WORLD_MIN <= f <= WORLD_MAX and not (f != f)  # not NaN

def read_xyz(addr):
    """Read one XYZ world-space vertex. Returns (x,y,z) or None."""
    try:
        data = dme.read_bytes(addr, 12)
        x, y, z = struct.unpack('>fff', data)
        if is_world_float(x) and is_world_float(y) and is_world_float(z):
            return (x, y, z)
    except Exception:
        pass
    return None

# ══════════════════════════════════════════════════════════════
# FIND VERTEX BUFFER BASE
# ══════════════════════════════════════════════════════════════
def find_vertex_buffer():
    """
    Use draw command offsets to solve for vertex buffer base.
    For each draw command offset O:
        Try candidate_base = candidate_vertex_addr - O
    If multiple commands agree on same base → found it.
    """
    print("\n  Deriving vertex buffer base from draw command offsets...")

    cmds = read_draw_commands()
    if not cmds:
        print("  No draw commands found.")
        return None, 0

    scan_start = DRAW_CMD_TABLE - 0x20000
    scan_end   = DRAW_CMD_TABLE + 0x20000

    base_hits = {}

    for bone_idx, vtx_off, _ in cmds[:32]:  # sample first 32
        for addr in range(scan_start, scan_end, 0x10):
            xyz = read_xyz(addr)
            if xyz:
                candidate_base = addr - vtx_off
                base_hits[candidate_base] = base_hits.get(candidate_base, 0) + 1

    if not base_hits:
        print("  No candidates found.")
        return None, 0

    best_base = max(base_hits, key=base_hits.get)
    best_score = base_hits[best_base]

    print(f"\n  Best base: 0x{best_base:08X}  (matches={best_score})")
    return best_base, best_score
# ══════════════════════════════════════════════════════════════
# READ DRAW COMMAND TABLE
# ══════════════════════════════════════════════════════════════

def read_draw_commands(max_rows=256):
    """
    Parse the draw command table starting at DRAW_CMD_TABLE.
    Returns list of (bone_idx, vtx_offset) pairs.
    Stops at the 0xFFFF sentinel row.
    """
    cmds = []
    addr = DRAW_CMD_TABLE
    for _ in range(max_rows):
        try:
            w0 = ru16(addr)
            w1 = ru16(addr + 2)
            w6 = ru16(addr + 12)
            w7 = ru16(addr + 14)
        except Exception:
            break
        
        if w0 == 0xFFFF:
            break
        
        bone_idx  = w0 & 0xFF      # low byte seems to vary
        vtx_off   = w7             # col[7] = vertex buffer offset
        vtx_count = w0 >> 8        # tentative
        
        cmds.append((bone_idx, vtx_off, addr))
        addr += DRAW_CMD_STRIDE
    
    return cmds

# ══════════════════════════════════════════════════════════════
# BOUNDING BOX TRACKER
# ══════════════════════════════════════════════════════════════

class BBox:
    def __init__(self, name):
        self.name = name
        self.reset()
    
    def reset(self):
        self.min_x = self.min_y = self.min_z =  1e9
        self.max_x = self.max_y = self.max_z = -1e9
        self.count = 0
    
    def add(self, x, y, z):
        self.min_x = min(self.min_x, x)
        self.max_x = max(self.max_x, x)
        self.min_y = min(self.min_y, y)
        self.max_y = max(self.max_y, y)
        self.min_z = min(self.min_z, z)
        self.max_z = max(self.max_z, z)
        self.count += 1
    
    def valid(self):
        return self.count > 0
    
    def center(self):
        return ((self.min_x+self.max_x)/2,
                (self.min_y+self.max_y)/2,
                (self.min_z+self.max_z)/2)
    
    def size(self):
        return (self.max_x-self.min_x,
                self.max_y-self.min_y,
                self.max_z-self.min_z)
    
    def __str__(self):
        if not self.valid(): return f"{self.name}: empty"
        cx,cy,cz = self.center()
        sx,sy,sz = self.size()
        return (f"{self.name:20s}  "
                f"center=({cx:6.3f},{cy:6.3f},{cz:6.3f})  "
                f"size=({sx:.3f}x{sy:.3f}x{sz:.3f})  "
                f"n={self.count}")

# ══════════════════════════════════════════════════════════════
# LIVE VERTEX READER
# ══════════════════════════════════════════════════════════════

def live_reader(vtx_base):
    """
    Read all vertices from the buffer every frame.
    Compute overall bounding box and per-region breakdown.
    Press Q to quit, S to save snapshot, D to dump all vertices.
    """
    import msvcrt

    print(f"\n  Vertex buffer base: 0x{vtx_base:08X}")
    print(f"  First vertex:       0x{vtx_base + FIRST_VTX_OFFSET:08X}")
    print(f"  Stride:             0x{VERTEX_STRIDE:02X}")
    print(f"  Controls: Q=quit  S=save snapshot  D=dump all vertices  R=reset stats\n")

    # Read draw commands once to know which offsets to sample
    cmds = read_draw_commands()
    print(f"  Found {len(cmds)} draw commands in table")
    
    if not cmds:
        print("  No draw commands found. Check DRAW_CMD_TABLE address.")
        input("  Press Enter."); return

    frame    = 0
    all_bbox = BBox("ALL_VERTICES")
    snapshots = []

    while True:
        # Check for keypress (non-blocking)
        if msvcrt.kbhit():
            ch = msvcrt.getch().lower()
            if ch == b'q':
                break
            elif ch == b's':
                snap = {
                    'frame': frame,
                    'bbox': {
                        'min': (all_bbox.min_x, all_bbox.min_y, all_bbox.min_z),
                        'max': (all_bbox.max_x, all_bbox.max_y, all_bbox.max_z),
                        'center': all_bbox.center(),
                        'size': all_bbox.size(),
                        'count': all_bbox.count,
                    }
                }
                snapshots.append(snap)
                print(f"\n  [SNAP {len(snapshots)}] {all_bbox}")
            elif ch == b'd':
                _dump_vertices(vtx_base, cmds)
            elif ch == b'r':
                all_bbox.reset()
                frame = 0
                print("\n  [RESET]")

        # Read vertices for this frame
        all_bbox.reset()
        verts_read = 0

        for bone_idx, vtx_off, cmd_addr in cmds:
            if verts_read >= MAX_VERTICES:
                break
            vtx_addr = vtx_base + vtx_off
            xyz = read_xyz(vtx_addr)
            if xyz:
                all_bbox.add(*xyz)
                verts_read += 1

        # Display
        if all_bbox.valid():
            cx, cy, cz = all_bbox.center()
            sx, sy, sz = all_bbox.size()
            line = (f"  frame={frame:5d}  verts={verts_read:3d}  "
                    f"X:[{all_bbox.min_x:6.3f}..{all_bbox.max_x:6.3f}]  "
                    f"Y:[{all_bbox.min_y:6.3f}..{all_bbox.max_y:6.3f}]  "
                    f"Z:[{all_bbox.min_z:6.3f}..{all_bbox.max_z:6.3f}]  "
                    f"center=({cx:.3f},{cy:.3f},{cz:.3f})  "
                    f"Q/S/D/R")
        else:
            line = f"  frame={frame:5d}  no valid vertices read  Q to quit"

        sys.stdout.write('\r' + line + '   ')
        sys.stdout.flush()
        frame += 1
        time.sleep(1/60)  # ~60fps polling

    print(f"\n\n  Session ended. {len(snapshots)} snapshots taken.")
    if snapshots:
        import json
        with open('vertex_snapshots.json', 'w') as f:
            json.dump(snapshots, f, indent=2)
        print(f"  Saved → vertex_snapshots.json")

def _dump_vertices(vtx_base, cmds, filename="vertices_dump.txt"):
    print(f"\n  Dumping all vertices...")
    with open(filename, 'w') as f:
        f.write(f"TvC Vertex Dump  base=0x{vtx_base:08X}\n{'='*60}\n")
        for i, (bone_idx, vtx_off, cmd_addr) in enumerate(cmds):
            vtx_addr = vtx_base + vtx_off
            xyz = read_xyz(vtx_addr)
            status = f"({xyz[0]:.4f}, {xyz[1]:.4f}, {xyz[2]:.4f})" if xyz else "INVALID"
            f.write(f"cmd_{i:03d}  cmd=0x{cmd_addr:08X}  "
                    f"bone={bone_idx:3d}  "
                    f"vtx=0x{vtx_addr:08X}  "
                    f"xyz={status}\n")
    print(f"  Saved → {filename}")

# ══════════════════════════════════════════════════════════════
# MANUAL BASE ENTRY
# ══════════════════════════════════════════════════════════════

def manual_probe():
    """
    Given a candidate vertex buffer base, probe it interactively.
    Useful when auto-scan doesn't find it.
    """
    print("\n  ─── MANUAL VERTEX BUFFER PROBE ───")
    print("  Enter candidate base addresses to test.")
    print("  Will read vertex at base+0x1BC8 and check if it's a valid XYZ.\n")

    while True:
        raw = input("  Base address (hex, or Q to back): ").strip()
        if raw.lower() == 'q': return None

        try:
            base = int(raw, 16)
        except ValueError:
            print("  Bad hex."); continue

        print(f"\n  Testing 0x{base:08X}...")
        for i in range(16):
            vtx_addr = base + FIRST_VTX_OFFSET + i * VERTEX_STRIDE
            xyz = read_xyz(vtx_addr)
            status = f"({xyz[0]:.4f}, {xyz[1]:.4f}, {xyz[2]:.4f})" if xyz else "INVALID"
            print(f"    vtx[{i:2d}]  0x{vtx_addr:08X}  {status}")

        ok = input(f"\n  Use 0x{base:08X} as vertex buffer base? (y/N): ").strip().lower()
        if ok == 'y':
            return base

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print("""
╔══════════════════════════════════════════════════════════════╗
║         TvC Vertex Reader  ,  hitbox from geometry           ║
╚══════════════════════════════════════════════════════════════╝
  Draw command table: 0x924707C0
  Goal: find vertex buffer base, read XYZ, compute bounding boxes
""")
    while True:
        print("  ─── MENU ──────────────────────────────────")
        print("    [1] Auto-scan for vertex buffer base")
        print("    [2] Manual probe (enter base address)")
        print("    [3] Live reader (enter known base)")
        print("    [4] Dump draw command table")
        print("    [Q] Quit")
        ch = input("\n    Choice: ").strip().lower()

        if ch == '1':
            base, score = find_vertex_buffer()
            if score >= 4:
                print(f"\n  Found! Score={score}")
                go = input(f"  Start live reader at 0x{base:08X}? (y/N): ").strip().lower()
                if go == 'y':
                    live_reader(base)
            else:
                print(f"  Low confidence (score={score}). Try manual probe.")

        elif ch == '2':
            base = manual_probe()
            if base:
                go = input(f"  Start live reader at 0x{base:08X}? (y/N): ").strip().lower()
                if go == 'y':
                    live_reader(base)

        elif ch == '3':
            raw = input("  Known vertex buffer base (hex): ").strip()
            try:
                base = int(raw, 16)
                live_reader(base)
            except ValueError:
                print("  Bad hex.")

        elif ch == '4':
            cmds = read_draw_commands()
            print(f"\n  {len(cmds)} draw commands:")
            for i, (bone, off, addr) in enumerate(cmds[:32]):
                print(f"    [{i:03d}]  cmd=0x{addr:08X}  bone={bone:3d}  vtx_offset=0x{off:04X}")
            if len(cmds) > 32:
                print(f"    ... and {len(cmds)-32} more")

        elif ch == 'q':
            print("\n  Bye.\n"); break

if __name__ == "__main__":
    try:
        dme.hook()
        print("[OK] Hooked to Dolphin.")
    except Exception as e:
        sys.exit(f"[ERROR] {e}")
    main()