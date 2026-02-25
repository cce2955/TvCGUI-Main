"""
╔══════════════════════════════════════════════════════════════╗
║           TvC Bone Toolkit  ,  all-in-one edition           ║
╚══════════════════════════════════════════════════════════════╝
Confirmed layout (3x4 row-major float matrix, stride 0x40):
  +0x0C  tX   +0x1C  tY   +0x2C  tZ
  +0x30–+0x3C  metadata  ← never write here

Requires: dolphin_memory_engine  (Windows, no other installs)
"""

import struct, sys, msvcrt, os, json
from collections import defaultdict

try:
    import dolphin_memory_engine as dme
except ImportError:
    sys.exit("[ERROR] dolphin_memory_engine not installed.")

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════

BONE_STRIDE  = 0x40
OFF_TX, OFF_TY, OFF_TZ = 0x0C, 0x1C, 0x2C
FLOAT_ONE    = 0x3F800000
MEM2_START   = 0x90000000
MEM2_END     = 0x93FFFFFF
CHUNK_SIZE   = 0x10000
CLUSTER_GAP  = 0x200
MIN_VALID_FL = 8
LABEL_FILE   = "bone_labels.json"

# How many consecutive dead/zero bones to tolerate before stopping
# the live finder. Higher = more permissive, finds wider skeletons.
LIVE_FINDER_TOLERANCE = 8

MATRIX_FIELDS = [
    (0x00,"m00"),(0x04,"m01"),(0x08,"m02"),(0x0C,"m03/tX"),
    (0x10,"m10"),(0x14,"m11"),(0x18,"m12"),(0x1C,"m13/tY"),
    (0x20,"m20"),(0x24,"m21"),(0x28,"m22"),(0x2C,"m23/tZ"),
]
META_FIELDS = [(0x30,"meta0"),(0x34,"meta1"),(0x38,"meta2"),(0x3C,"meta3")]

REGIONS = {
    0: ("P1 skeleton",     0x925BDF40, 108),
    1: ("P2 skeleton",     0x925CDF40, 108),
    2: ("Cluster 1229",    0x9250CFC0, 106),
    3: ("Cluster 1613",    0x92C06000, 107),
    4: ("Cluster 1434",    0x928A7780, 110),
    5: ("Cluster 1881",    0x92F8C000, 180),
    6: ("Original region", 0x921773C0,  50000),
    #6: ("Original region", 0x924773C0,  300),
    # slot 99 reserved for live finder result , filled at runtime
}

INC_MAP     = {b'1': 1.0, b'5': 5.0, b'0': 10.0, b'2': 20.0}
ARROW_UP    = 0x48
ARROW_DOWN  = 0x50
ARROW_LEFT  = 0x4B
ARROW_RIGHT = 0x4D

# ══════════════════════════════════════════════════════════════
# LOW-LEVEL I/O
# ══════════════════════════════════════════════════════════════

def _r32(addr):
    return struct.unpack('>f', dme.read_bytes(addr, 4))[0]

def _w32(addr, val):
    dme.write_bytes(addr, struct.pack('>f', val))

def _ru32(addr):
    return struct.unpack('>I', dme.read_bytes(addr, 4))[0]

def u32_buf(data, off):
    if off + 4 > len(data): return None
    return struct.unpack_from('>I', data, off)[0]

def f32_buf(data, off):
    raw = u32_buf(data, off)
    if raw is None: return None
    return struct.unpack('>f', struct.pack('>I', raw))[0]

def is_valid_float(raw):
    if raw is None: return False
    exp = (raw >> 23) & 0xFF
    if exp == 0xFF: return False
    if exp == 0x00 and (raw & 0x7FFFFF) != 0: return False
    return abs(struct.unpack('>f', struct.pack('>I', raw))[0]) < 1e6

def read_key():
    ch = msvcrt.getch()
    if ch in (b'\x00', b'\xe0'):
        return ('arrow', ord(msvcrt.getch()))
    return ('char', ch)

# ══════════════════════════════════════════════════════════════
# BONE OPS
# ══════════════════════════════════════════════════════════════

def get_translation(base):
    return _r32(base+OFF_TX), _r32(base+OFF_TY), _r32(base+OFF_TZ)

def set_translation(base, tx, ty, tz):
    _w32(base+OFF_TX, tx); _w32(base+OFF_TY, ty); _w32(base+OFF_TZ, tz)

def nudge(base, axis, delta):
    tx, ty, tz = get_translation(base)
    if   axis == 'x': tx += delta
    elif axis == 'y': ty += delta
    elif axis == 'z': tz += delta
    set_translation(base, tx, ty, tz)

def zero_bone(base):
    dme.write_bytes(base, b'\x00' * BONE_STRIDE)

def restore_identity(base):
    for i, v in enumerate([1.,0.,0.,0., 0.,1.,0.,0., 0.,0.,1.,0.]):
        _w32(base + i*4, v)

# ══════════════════════════════════════════════════════════════
# DUMP
# ══════════════════════════════════════════════════════════════

def dump_bone(base, label=""):
    tag = f"  [{label}]" if label else ""
    print(f"\n  0x{base:08X}{tag}")
    print(f"  {'Off':<6} {'Hex':>10}  {'Float':>12}  Field")
    print(f"  {'-'*50}")
    for off, name in MATRIX_FIELDS:
        raw  = _ru32(base+off)
        fval = _r32(base+off)
        t = " ← tX" if off==OFF_TX else " ← tY" if off==OFF_TY else " ← tZ" if off==OFF_TZ else ""
        print(f"  +0x{off:02X}  0x{raw:08X}  {fval:12.5f}  {name}{t}")
    print(f"  -- metadata (read-only) --")
    for off, name in META_FIELDS:
        print(f"  +0x{off:02X}  0x{_ru32(base+off):08X}  {name}")

def dump_region(ridx):
    name, start, count = REGIONS[ridx]
    print(f"\n  === {name}  0x{start:08X}  ({count} bones) ===")
    for i in range(count):
        dump_bone(start + i*BONE_STRIDE, f"bone_{i}")

# ══════════════════════════════════════════════════════════════
# REGION / BONE PICKER
# ══════════════════════════════════════════════════════════════

def print_regions():
    print()
    for i, (name, start, count) in REGIONS.items():
        print(f"    [{i}]  {name:<24}  0x{start:08X}  ({count} bones)")

def select_bone_address():
    print("\n  ─── SELECT BONE ──────────────────────────")
    print("    [A] Enter address manually")
    print("    [R] Pick from region + bone index")
    ch = input("\n    Choice: ").strip().upper()
    if ch == 'A':
        base = int(input("    Address (hex): ").strip(), 16)
        print(f"    tX→0x{base+OFF_TX:08X}  tY→0x{base+OFF_TY:08X}  tZ→0x{base+OFF_TZ:08X}")
        return base, None
    elif ch == 'R':
        print_regions()
        ri = int(input("\n    Region #: ").strip())
        name, rstart, rcount = REGIONS[ri]
        bi = int(input(f"    Bone index (0–{rcount-1}): ").strip())
        base = rstart + bi * BONE_STRIDE
        print(f"    → 0x{base:08X}")
        return base, (ri, bi)
    else:
        print("    Invalid.")
        return select_bone_address()

# ══════════════════════════════════════════════════════════════
# GRABBER
# ══════════════════════════════════════════════════════════════

def grabber():
    base, loc = select_bone_address()
    inc = 1.0
    ri, bi = loc if loc else (None, None)

    def label():
        if ri is not None and bi is not None:
            return f"[{REGIONS[ri][0]} bone#{bi}]"
        return ""

    print(f"\n  [GRABBED] 0x{base:08X} {label()}")
    print("  LR=X  UD=Y  WS=Z  | 1/5/0/2=inc | N/P=next/prev | R=reset D=dump G=grab Q=back\n")

    while True:
        tx, ty, tz = get_translation(base)
        line = (f"  0x{base:08X} {label()}  "
                f"tX={tx:8.3f}  tY={ty:8.3f}  tZ={tz:8.3f}  inc={inc:<4.0f}  "
                f"LR=X UD=Y WS=Z | 1/5/0/2 | N/P | R D G Q")
        sys.stdout.write('\r' + line + '   ')
        sys.stdout.flush()

        kind, val = read_key()
        if kind == 'arrow':
            if   val == ARROW_LEFT:  nudge(base, 'x', -inc)
            elif val == ARROW_RIGHT: nudge(base, 'x', +inc)
            elif val == ARROW_UP:    nudge(base, 'y', +inc)
            elif val == ARROW_DOWN:  nudge(base, 'y', -inc)
        elif kind == 'char':
            c = val.lower()
            if   c in INC_MAP:  inc = INC_MAP[c]
            elif c == b'w':     nudge(base, 'z', +inc)
            elif c == b's':     nudge(base, 'z', -inc)
            elif c == b'r':     set_translation(base, 0., 0., 0.)
            elif c == b'd':     print(); dump_bone(base, label()); print()
            elif c == b'n':
                base += BONE_STRIDE
                if bi is not None: bi += 1
                print(f"\n  → 0x{base:08X}\n")
            elif c == b'p':
                base -= BONE_STRIDE
                if bi is not None: bi -= 1
                print(f"\n  → 0x{base:08X}\n")
            elif c == b'g':
                print(); base, loc = select_bone_address()
                ri, bi = loc if loc else (None, None)
                print(f"\n  [GRABBED] 0x{base:08X}\n")
            elif c == b'q':
                print(); return

# ══════════════════════════════════════════════════════════════
# BONE TOOL
# ══════════════════════════════════════════════════════════════

def bone_tool():
    while True:
        print("\n  ─── BONE TOOL ──────────────────────────────")
        print("    [1] Dump single bone")
        print("    [2] Dump all bones in a region")
        print("    [3] Set translation (address)")
        print("    [4] Zero a bone  [DESTRUCTIVE]")
        print("    [5] Restore identity on a bone")
        print("    [6] Zero entire region  [VERY DESTRUCTIVE]")
        print("    [Q] Back")
        ch = input("\n    Choice: ").strip().lower()
        if ch == '1':
            base, _ = select_bone_address()
            dump_bone(base)
        elif ch == '2':
            print_regions()
            dump_region(int(input("    Region #: ").strip()))
        elif ch == '3':
            base, _ = select_bone_address()
            set_translation(base, float(input("    tX: ")),
                                  float(input("    tY: ")),
                                  float(input("    tZ: ")))
            print("    [WRITE] done")
        elif ch == '4':
            base, _ = select_bone_address()
            if input(f"    Zero 0x{base:08X}? (y/N): ").strip().lower() == 'y':
                zero_bone(base); print("    [ZERO] done")
            else:
                print("    Aborted.")
        elif ch == '5':
            base, _ = select_bone_address()
            restore_identity(base); print("    [IDENTITY] done")
        elif ch == '6':
            print_regions()
            ri = int(input("    Region #: ").strip())
            name, start, count = REGIONS[ri]
            if input(f"    Zero ALL {count} bones in '{name}'? (yes/N): ").strip() == 'yes':
                for i in range(count): zero_bone(start + i*BONE_STRIDE)
                print(f"    [ZERO ALL] {count} bones zeroed.")
            else:
                print("    Aborted.")
        elif ch == 'q':
            return

# ══════════════════════════════════════════════════════════════
# MEM2 SCANNER
# ══════════════════════════════════════════════════════════════

def is_bone_candidate(data, off):
    if off + BONE_STRIDE > len(data): return False
    raws = [u32_buf(data, off + i*4) for i in range(12)]
    if sum(1 for r in raws if is_valid_float(r)) < MIN_VALID_FL: return False
    if FLOAT_ONE not in raws: return False
    meta = [u32_buf(data, off + 0x30 + i*4) for i in range(4)]
    return sum(1 for r in meta if r is not None and not is_valid_float(r)) >= 1

def cluster_records(records):
    if not records: return []
    records = sorted(records)
    clusters, current = [], [records[0]]
    for addr in records[1:]:
        if addr - current[-1] <= CLUSTER_GAP: current.append(addr)
        else: clusters.append(current); current = [addr]
    clusters.append(current)
    return clusters

def mem2_scan():
    out_file = "mem2_bones.txt"
    print(f"\n  Scanning MEM2: 0x{MEM2_START:08X}–0x{MEM2_END:08X}")
    print("  This will take a few minutes...\n")
    total = (MEM2_END - MEM2_START + CHUNK_SIZE - 1) // CHUNK_SIZE
    hits, errors = [], 0
    for ci in range(total):
        cs = MEM2_START + ci * CHUNK_SIZE
        ce = min(cs + CHUNK_SIZE, MEM2_END)
        sys.stdout.write(f"\r  [{ci/total*100:5.1f}%]  0x{cs:08X}  hits={len(hits)}  err={errors}   ")
        sys.stdout.flush()
        try:
            data  = dme.read_bytes(cs, ce - cs)
            align = (0x40 - (cs % 0x40)) % 0x40
            off   = align
            while off + BONE_STRIDE <= len(data):
                if is_bone_candidate(data, off): hits.append(cs + off)
                off += BONE_STRIDE
        except Exception:
            errors += 1
    print(f"\n\n  [DONE] {len(hits)} records  {errors} errors")
    clusters = cluster_records(hits)
    lines = [f"TvC MEM2 Scan  {len(hits)} records  {len(clusters)} clusters", "="*60]
    for ci2, cl in enumerate(clusters):
        span = cl[-1] - cl[0] + BONE_STRIDE
        lines.append(f"\nCluster [{ci2:04d}]  0x{cl[0]:08X}–0x{cl[-1]:08X}  ({len(cl)} records, 0x{span:X} bytes)")
        for addr in cl: lines.append(f"  0x{addr:08X}")
    with open(out_file, 'w') as f: f.write('\n'.join(lines))
    print("  Clusters 8+ records:")
    for ci2, cl in enumerate(clusters):
        if len(cl) >= 8:
            print(f"    [{ci2:04d}]  0x{cl[0]:08X}–0x{cl[-1]:08X}  {len(cl)} records")
    print(f"\n  Saved → {os.path.abspath(out_file)}")
    input("\n  Press Enter to continue.")

# ══════════════════════════════════════════════════════════════
# LIVE REGION FINDER
# Walks outward from seed, tolerates gaps of dead bones
# ══════════════════════════════════════════════════════════════

def find_live_region():
    print("\n  ─── LIVE REGION FINDER ─────────────────────")
    print(f"  Tolerance: {LIVE_FINDER_TOLERANCE} consecutive dead bones before stopping")
    print("  (edit LIVE_FINDER_TOLERANCE at top of file to tune)\n")

    raw  = input("  Known-live bone address (hex): ").strip()
    seed = (int(raw, 16) // BONE_STRIDE) * BONE_STRIDE

    if not is_live_record(seed):
        print(f"  WARNING: 0x{seed:08X} reads as zero. Try 0x924773C0.")
        input("  Press Enter to continue."); return

    print(f"  Seed: 0x{seed:08X}  Scanning backward...")

    # Walk backward with tolerance
    start       = seed
    dead_streak = 0
    candidate   = seed - BONE_STRIDE

    while candidate >= MEM2_START:
        start = candidate
        candidate -= BONE_STRIDE
        sys.stdout.write(f"\r  start=0x{start:08X}  dead_streak={dead_streak}   ")
        sys.stdout.flush()

    print(f"\n  Scanning forward...")

    # Walk forward with tolerance
    end         = seed
    dead_streak = 0
    candidate   = seed + BONE_STRIDE

    while candidate + BONE_STRIDE <= MEM2_END:
        end = candidate
        candidate += BONE_STRIDE
        sys.stdout.write(f"\r  end=0x{end:08X}  dead_streak={dead_streak}   ")
        sys.stdout.flush()

    count = (end - start) // BONE_STRIDE + 1
    span  = end - start + BONE_STRIDE

    print(f"\n\n  ══ LIVE SKELETON FOUND ══")
    print(f"  Start:  0x{start:08X}")
    print(f"  End:    0x{end:08X}")
    print(f"  Bones:  {count}  (includes zero/dead slots within range)")
    print(f"  Span:   0x{span:X} bytes")

    # Register as runtime region 99
    REGIONS[99] = ("Live skeleton", start, count)
    print(f"\n  Registered as region [99] for labeler and grabber.")

    if input("\n  Save full dump to live_skeleton.txt? (y/N): ").strip().lower() == 'y':
        _dump_range_to_file(start, count, "live_skeleton.txt")

    input("\n  Press Enter to continue.")

def _dump_range_to_file(start, count, filename):
    labels = load_labels()
    with open(filename, 'w') as f:
        f.write(f"Live skeleton  0x{start:08X}  ({count} bones)\n{'='*60}\n")
        for i in range(count):
            addr = start + i * BONE_STRIDE
            live = is_live_record(addr)
            lbl  = labels.get(hex(addr), "")
            lbl_tag = f"  [{lbl}]" if lbl else ""
            f.write(f"\nbone_{i:03d}  0x{addr:08X}  {'[LIVE]' if live else '[dead]'}{lbl_tag}\n")
            try:
                data = dme.read_bytes(addr, BONE_STRIDE)
                for off, name in MATRIX_FIELDS:
                    r = u32_buf(data, off); v = f32_buf(data, off)
                    t = " <tX" if off==OFF_TX else " <tY" if off==OFF_TY else " <tZ" if off==OFF_TZ else ""
                    rs = f"0x{r:08X}" if r is not None else "N/A        "
                    vs = f"{v:12.5f}" if v is not None else "         N/A"
                    f.write(f"  +0x{off:02X}  {rs}  {vs}  {name}{t}\n")
                for off, name in META_FIELDS:
                    r = u32_buf(data, off)
                    f.write(f"  +0x{off:02X}  {'0x'+format(r,'08X') if r is not None else 'N/A'}  {name}\n")
            except Exception:
                f.write(f"  [READ ERROR]\n")
    print(f"  Saved → {filename}")

# ══════════════════════════════════════════════════════════════
# BONE LABELER  ,  works on any region including live finder result
# ══════════════════════════════════════════════════════════════

def load_labels():
    if os.path.exists(LABEL_FILE):
        with open(LABEL_FILE, 'r') as f: return json.load(f)
    return {}

def save_labels(labels):
    with open(LABEL_FILE, 'w') as f: json.dump(labels, f, indent=2)

def print_label_summary(labels, rstart, rcount):
    print(f"\n  Labels ({sum(1 for i in range(rcount) if hex(rstart+i*BONE_STRIDE) in labels)} of {rcount}):")
    print(f"  {'#':<5} {'Address':<14} Label")
    print(f"  {'-'*50}")
    for i in range(rcount):
        addr = rstart + i * BONE_STRIDE
        lbl  = labels.get(hex(addr), ",")
        print(f"  {i:<5} 0x{addr:08X}    {lbl}")

def bone_labeler():
    print("\n  ─── BONE LABELER ───────────────────────────")
    print("  Zeros one bone, you observe, type label, restores, moves on.")
    print("  Works on any region , original 13 bones OR live finder result.\n")

    # Pick region
    print_regions()
    ri_input = input("\n  Region # to label: ").strip()
    ri = int(ri_input)
    if ri not in REGIONS:
        print("  Region not found. Run Live Finder (option 4) first if using 99.")
        input("  Press Enter."); return

    rname, rstart, rcount = REGIONS[ri]
    print(f"\n  Region: {rname}  0x{rstart:08X}  ({rcount} bones)")

    labels    = load_labels()
    start_idx = 0

    if labels:
        labeled_in_region = sum(1 for i in range(rcount)
                                if hex(rstart + i*BONE_STRIDE) in labels)
        if labeled_in_region > 0:
            print_label_summary(labels, rstart, rcount)
            print(f"\n  {labeled_in_region} bones already labeled in this region.")
            print("    [C] Continue from first unlabeled")
            print("    [M] Modify a specific bone")
            print("    [R] Relabel entire region from scratch")
            print("    [Q] Back")
            ch = input("\n  Choice: ").strip().upper()
            if ch == 'Q': return
            elif ch == 'R':
                # Clear only this region's labels
                for i in range(rcount):
                    k = hex(rstart + i * BONE_STRIDE)
                    if k in labels: del labels[k]
                save_labels(labels); start_idx = 0
            elif ch == 'M':
                bi = int(input("  Bone index to modify: ").strip())
                addr = rstart + bi * BONE_STRIDE
                key  = hex(addr)
                print(f"  Current: {labels.get(key, '(none)')}")
                new  = input("  New label (blank to clear): ").strip()
                if new: labels[key] = new
                elif key in labels: del labels[key]
                save_labels(labels)
                bone_labeler(); return
            elif ch == 'C':
                for i in range(rcount):
                    if hex(rstart + i * BONE_STRIDE) not in labels:
                        start_idx = i; break
                else:
                    print("  All bones in this region already labeled!")
                    input("  Press Enter."); return

    print(f"\n  Starting at bone {start_idx} of {rcount}.")
    print("  Inputs: label | 'skip' | 'nothing' | 'back' | 'done'")
    input("  Press Enter to begin...\n")

    i = start_idx
    while i < rcount:
        addr = rstart + i * BONE_STRIDE
        key  = hex(addr)

        try:
            original = dme.read_bytes(addr, BONE_STRIDE)
        except Exception:
            print(f"  [READ ERROR] bone_{i} , skipping")
            i += 1; continue

        zero_bone(addr)
        pct = int((i / rcount) * 20)
        bar = "█" * pct + "░" * (20 - pct)
        print(f"\n  [{bar}] bone_{i}/{rcount-1}  0x{addr:08X}  ZEROED")
        print("  What happened on screen?")
        print("  > ", end='', flush=True)
        lbl = input().strip()
        dme.write_bytes(addr, original)
        print("  [RESTORED]")

        if lbl.lower() == 'done':
            break
        elif lbl.lower() == 'back':
            if i > start_idx:
                i -= 1
                prev_key = hex(rstart + i * BONE_STRIDE)
                if prev_key in labels: del labels[prev_key]
                save_labels(labels)
            continue
        elif lbl.lower() == 'skip':
            i += 1; continue
        else:
            
            # blank label becomes "dead" (NOT "nothing"), and any typed label overwrites it.
            labels[key] = lbl if lbl else 'dead'
            save_labels(labels)
            i += 1

    print(f"\n  ══ SESSION COMPLETE ══")
    print_label_summary(labels, rstart, rcount)

    # Save human-readable map
    map_file = f"bone_map_{rname.replace(' ','_')}.txt"
    with open(map_file, 'w') as f:
        f.write(f"TvC Bone Map , {rname}  0x{rstart:08X}\n{'='*50}\n")
        for ii in range(rcount):
            a = rstart + ii * BONE_STRIDE
            l = labels.get(hex(a), "unlabeled")
            f.write(f"bone_{ii:03d}  0x{a:08X}  {l}\n")

    print(f"\n  {map_file} + {LABEL_FILE} saved.")
    input("\n  Press Enter to continue.")

# ══════════════════════════════════════════════════════════════
# MAIN MENU
# ══════════════════════════════════════════════════════════════

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║              TvC Bone Toolkit  ,  all-in-one                ║
║      Grabber | Dump | Scan | Zero | Restore | Labeler       ║
╚══════════════════════════════════════════════════════════════╝
  Stride: 0x40   tX=+0x0C   tY=+0x1C   tZ=+0x2C
  Original: 0x924773C0 (13 bones, confirmed live, affects Ryu)
"""

def main_menu():
    print(BANNER)
    while True:
        print("  ─── MAIN MENU ──────────────────────────────")
        print("    [1] Grabber        (live keyboard bone editor)")
        print("    [2] Bone tool       (dump / zero / restore)")
        print("    [3] MEM2 full scan  (saves mem2_bones.txt)")
        print("    [4] Live finder     (walk outward, find full skeleton)")
        print("    [5] Bone labeler    (zero each bone, you name it)")
        print("    [Q] Quit")
        ch = input("\n    Choice: ").strip().lower()
        if   ch == '1': grabber()
        elif ch == '2': bone_tool()
        elif ch == '3': mem2_scan()
        elif ch == '4': find_live_region()
        elif ch == '5': bone_labeler()
        elif ch == 'q': print("\n  Bye.\n"); break
        else: print("    Unknown option.")

if __name__ == "__main__":
    try:
        dme.hook()
        print("[OK] Hooked to Dolphin.")
    except Exception as e:
        sys.exit(f"[ERROR] {e}")
    main_menu()