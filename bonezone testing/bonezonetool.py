"""
TvC Bone Tool  v2
=================
Dump / zero / restore bone records.
Updated to confirmed 0x40-stride 3x4 matrix layout.
SCAN range now covers full MEM2.

Requires: dolphin_memory_engine
"""

import struct
import sys

try:
    import dolphin_memory_engine as dme
except ImportError:
    sys.exit("[ERROR] dolphin_memory_engine not installed.")

# ─────────────────────────────────────────────
# CONFIG  —  update SCAN_START/END after running tvc_bone_finder.py
# ─────────────────────────────────────────────

SCAN_START  = 0x924773C0   # update after wide scan
SCAN_END    = 0x92477600
BONE_STRIDE = 0x40

# 3x4 matrix field offsets
OFF_TX = 0x0C
OFF_TY = 0x1C
OFF_TZ = 0x2C

MATRIX_FIELDS = [
    (0x00,"m00"),(0x04,"m01"),(0x08,"m02"),(0x0C,"m03/tX"),
    (0x10,"m10"),(0x14,"m11"),(0x18,"m12"),(0x1C,"m13/tY"),
    (0x20,"m20"),(0x24,"m21"),(0x28,"m22"),(0x2C,"m23/tZ"),
]
META_FIELDS = [(0x30,"meta0"),(0x34,"meta1"),(0x38,"meta2"),(0x3C,"meta3")]

KNOWN_BONES = {
    0: 0x924773C0,
    1: 0x92477440,
    2: 0x924774C0,
    3: 0x92477540,
}

# ─────────────────────────────────────────────
# LOW-LEVEL
# ─────────────────────────────────────────────

def _r32(addr):
    return struct.unpack('>f', dme.read_bytes(addr, 4))[0]

def _w32(addr, val):
    dme.write_bytes(addr, struct.pack('>f', val))

def _ru32(addr):
    return struct.unpack('>I', dme.read_bytes(addr, 4))[0]

def get_translation(base):
    return _r32(base+OFF_TX), _r32(base+OFF_TY), _r32(base+OFF_TZ)

def set_translation(base, tx, ty, tz):
    _w32(base+OFF_TX, tx)
    _w32(base+OFF_TY, ty)
    _w32(base+OFF_TZ, tz)

# ─────────────────────────────────────────────
# DUMP
# ─────────────────────────────────────────────

def dump_bone(base, label=""):
    tag = f" [{label}]" if label else ""
    print(f"\n0x{base:08X}{tag}")
    print(f"  {'Off':<6} {'Hex':>10}  {'Float':>12}  Field")
    print(f"  {'-'*48}")
    for off, name in MATRIX_FIELDS:
        raw  = _ru32(base+off)
        fval = _r32(base+off)
        tx_tag = " ← tX" if off==OFF_TX else " ← tY" if off==OFF_TY else " ← tZ" if off==OFF_TZ else ""
        print(f"  +0x{off:02X}  0x{raw:08X}  {fval:12.5f}  {name}{tx_tag}")
    print(f"  -- metadata (read-only) --")
    for off, name in META_FIELDS:
        raw = _ru32(base+off)
        print(f"  +0x{off:02X}  0x{raw:08X}  {name}")

def dump_all():
    addr = SCAN_START
    i = 0
    while addr + BONE_STRIDE <= SCAN_END:
        tag = f"bone_{i}" + (" *" if addr in KNOWN_BONES.values() else "")
        dump_bone(addr, tag)
        addr += BONE_STRIDE
        i += 1

# ─────────────────────────────────────────────
# WRITE OPS
# ─────────────────────────────────────────────

def zero_bone(base, confirm=True):
    if confirm:
        if input(f"Zero 0x{base:08X}? (y/N): ").strip().lower() != 'y':
            print("Aborted.")
            return
    dme.write_bytes(base, b'\x00' * BONE_STRIDE)
    print(f"[ZERO] 0x{base:08X}")

def restore_identity(base):
    # Identity 3x4 matrix: diagonal 1s, translation 0
    identity = [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
    ]
    for i, val in enumerate(identity):
        _w32(base + i*4, val)
    print(f"[IDENTITY] 0x{base:08X}")

# ─────────────────────────────────────────────
# MENU
# ─────────────────────────────────────────────

def menu():
    print("\n=== TvC Bone Tool ===")
    opts = {
        "1": "Dump all bones in range",
        "2": "Dump single bone",
        "3": "Set translation on a bone",
        "4": "Zero a bone (collapse test)",
        "5": "Restore identity on a bone",
        "q": "Quit",
    }
    for k,v in opts.items():
        print(f"  {k}) {v}")
    choice = input("\nChoice: ").strip().lower()

    if choice == "1":
        dump_all()
    elif choice == "2":
        base = int(input("Address (hex): ").strip(), 16)
        dump_bone(base)
    elif choice == "3":
        base = int(input("Address (hex): ").strip(), 16)
        tx = float(input("tX: "))
        ty = float(input("tY: "))
        tz = float(input("tZ: "))
        set_translation(base, tx, ty, tz)
        print(f"[WRITE] done")
    elif choice == "4":
        base = int(input("Address (hex): ").strip(), 16)
        zero_bone(base)
    elif choice == "5":
        base = int(input("Address (hex): ").strip(), 16)
        restore_identity(base)
    elif choice == "q":
        return
    else:
        print("Unknown option.")

    menu()

# ─────────────────────────────────────────────

if __name__ == "__main__":
    try:
        dme.hook()
        print("[OK] Hooked to Dolphin.")
    except Exception as e:
        sys.exit(f"[ERROR] {e}")
    menu()