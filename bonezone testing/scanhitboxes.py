"""
TvC Bone Grabber  v3  ,  3x4 matrix edition
=============================================
Layout confirmed: each bone record is a 3x4 row-major float matrix.
Translation lives in column 3 of each row:
  +0x0C  tX  (row 0, col 3)
  +0x1C  tY  (row 1, col 3)
  +0x2C  tZ  (row 2, col 3)

Stride: 0x40 (64 bytes) per record.
Metadata (non-float) lives at +0x30 onward , never touched.

Controls:
  LEFT / RIGHT  →  tX
  UP   / DOWN   →  tY
  W    / S      →  tZ
  1 / 5 / 0 / 2 →  increment 1 / 5 / 10 / 20
  R             →  reset translation to 0,0,0
  D             →  dump full matrix of current bone
  G             →  grab different bone
  Q             →  quit

Requires: dolphin_memory_engine  (no other installs)
"""

import struct
import sys
import msvcrt

try:
    import dolphin_memory_engine as dme
except ImportError:
    sys.exit("[ERROR] dolphin_memory_engine not installed.")

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

SCAN_START  = 0x924773C0
SCAN_END    = 0x92477600
BONE_STRIDE = 0x40

# Translation = column 3 of the 3x4 matrix
OFF_TX = 0x0C   # row 0 translation
OFF_TY = 0x1C   # row 1 translation
OFF_TZ = 0x2C   # row 2 translation

# Full matrix field offsets (for display)
MATRIX_OFFSETS = [
    (0x00, "m00"), (0x04, "m01"), (0x08, "m02"), (0x0C, "m03/tX"),
    (0x10, "m10"), (0x14, "m11"), (0x18, "m12"), (0x1C, "m13/tY"),
    (0x20, "m20"), (0x24, "m21"), (0x28, "m22"), (0x2C, "m23/tZ"),
]

# Metadata offsets (read-only display, never written)
META_OFFSETS = [0x30, 0x34, 0x38, 0x3C]

KNOWN_BONES = {
    0: 0x924773C0,
    1: 0x92477440,
    2: 0x924774C0,
    3: 0x92477540,
}

INC_MAP = {b'1': 1.0, b'5': 5.0, b'0': 10.0, b'2': 20.0}

ARROW_UP    = 0x48
ARROW_DOWN  = 0x50
ARROW_LEFT  = 0x4B
ARROW_RIGHT = 0x4D

# ─────────────────────────────────────────────
# LOW-LEVEL
# ─────────────────────────────────────────────

def _read_f32(addr):
    return struct.unpack('>f', dme.read_bytes(addr, 4))[0]

def _write_f32(addr, val):
    dme.write_bytes(addr, struct.pack('>f', val))

def _read_u32(addr):
    return struct.unpack('>I', dme.read_bytes(addr, 4))[0]

def get_translation(base):
    return (
        _read_f32(base + OFF_TX),
        _read_f32(base + OFF_TY),
        _read_f32(base + OFF_TZ),
    )

def set_translation(base, tx, ty, tz):
    _write_f32(base + OFF_TX, tx)
    _write_f32(base + OFF_TY, ty)
    _write_f32(base + OFF_TZ, tz)

def nudge(base, axis, delta):
    tx, ty, tz = get_translation(base)
    if   axis == 'x': tx += delta
    elif axis == 'y': ty += delta
    elif axis == 'z': tz += delta
    set_translation(base, tx, ty, tz)

# ─────────────────────────────────────────────
# MATRIX DUMP
# ─────────────────────────────────────────────

def dump_matrix(base):
    print(f"\n  ── Matrix dump: 0x{base:08X} ──")
    print(f"  {'Off':<6} {'Raw':>10}  {'Float':>10}  Field")
    print(f"  {'-'*45}")
    for off, name in MATRIX_OFFSETS:
        raw  = _read_u32(base + off)
        fval = _read_f32(base + off)
        tag  = " <-- tX" if off == OFF_TX else " <-- tY" if off == OFF_TY else " <-- tZ" if off == OFF_TZ else ""
        print(f"  +0x{off:02X}  0x{raw:08X}  {fval:10.4f}  {name}{tag}")
    print(f"  -- metadata (read only) --")
    for off in META_OFFSETS:
        raw = _read_u32(base + off)
        print(f"  +0x{off:02X}  0x{raw:08X}")
    print()

# ─────────────────────────────────────────────
# KEY READER
# ─────────────────────────────────────────────

def read_key():
    ch = msvcrt.getch()
    if ch in (b'\x00', b'\xe0'):
        scan = ord(msvcrt.getch())
        return ('arrow', scan)
    return ('char', ch)

# ─────────────────────────────────────────────
# BONE SELECTION
# ─────────────────────────────────────────────

def scan_bones():
    bones = []
    addr = SCAN_START
    while addr + BONE_STRIDE <= SCAN_END:
        bones.append(addr)
        addr += BONE_STRIDE
    return bones

def select_bone():
    print("\n─────────────────────────────────")
    print("  SELECT BONE")
    print("─────────────────────────────────")
    print("  [A] Type address manually")
    print("  [B] Pick from known bone list")
    print("  [C] Pick from full scan list")
    choice = input("\n  Choice: ").strip().upper()

    if choice == 'A':
        raw = input("  Enter base address (hex): ").strip()
        base = int(raw, 16)
        print(f"  tX → 0x{base + OFF_TX:08X}")
        print(f"  tY → 0x{base + OFF_TY:08X}")
        print(f"  tZ → 0x{base + OFF_TZ:08X}")
        return base

    elif choice == 'B':
        print()
        for idx, base in sorted(KNOWN_BONES.items()):
            tx, ty, tz = get_translation(base)
            print(f"  [{idx}]  0x{base:08X}   tX={tx:8.3f}  tY={ty:8.3f}  tZ={tz:8.3f}")
        n = int(input("\n  Enter number: ").strip())
        return KNOWN_BONES[n]

    elif choice == 'C':
        bones = scan_bones()
        print()
        for i, base in enumerate(bones):
            tx, ty, tz = get_translation(base)
            tag = " *" if base in KNOWN_BONES.values() else ""
            print(f"  [{i:>2}]  0x{base:08X}{tag}   tX={tx:8.3f}  tY={ty:8.3f}  tZ={tz:8.3f}")
        n = int(input("\n  Enter number: ").strip())
        return bones[n]

    else:
        print("  Invalid choice.")
        return select_bone()

# ─────────────────────────────────────────────
# STATUS LINE
# ─────────────────────────────────────────────

def print_status(base, inc):
    tx, ty, tz = get_translation(base)
    line = (
        f"  0x{base:08X}  "
        f"tX={tx:8.3f}  tY={ty:8.3f}  tZ={tz:8.3f}  "
        f"inc={inc:<4.0f}  "
        f"LR=X UD=Y WS=Z | 1/5/0/2=inc | R=rst D=dump G=grab Q=quit"
    )
    sys.stdout.write('\r' + line + '   ')
    sys.stdout.flush()

# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────

def run():
    try:
        dme.hook()
        print("[OK] Hooked to Dolphin.")
    except Exception as e:
        sys.exit(f"[ERROR] Could not hook: {e}")

    base = select_bone()
    inc  = 1.0

    print(f"\n[GRABBED] 0x{base:08X}")
    print(f"  Layout: 3x4 matrix  tX=+0x0C  tY=+0x1C  tZ=+0x2C")
    print(f"  LEFT/RIGHT=X  UP/DOWN=Y  W/S=Z")
    print(f"  1/5/0/2=inc  R=reset  D=dump matrix  G=regrab  Q=quit\n")

    while True:
        print_status(base, inc)
        kind, val = read_key()

        if kind == 'arrow':
            if   val == ARROW_LEFT:  nudge(base, 'x', -inc)
            elif val == ARROW_RIGHT: nudge(base, 'x', +inc)
            elif val == ARROW_UP:    nudge(base, 'y', +inc)
            elif val == ARROW_DOWN:  nudge(base, 'y', -inc)

        elif kind == 'char':
            ch = val.lower()
            if   ch in INC_MAP: inc = INC_MAP[ch]
            elif ch == b'w':    nudge(base, 'z', +inc)
            elif ch == b's':    nudge(base, 'z', -inc)
            elif ch == b'r':    set_translation(base, 0.0, 0.0, 0.0)
            elif ch == b'd':
                print()
                dump_matrix(base)
            elif ch == b'g':
                print()
                base = select_bone()
                print(f"\n[GRABBED] 0x{base:08X}\n")
            elif ch == b'q':
                print("\n[EXIT]")
                break

if __name__ == "__main__":
    run()