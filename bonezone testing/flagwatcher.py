import struct
import sys
import time
import msvcrt
import dolphin_memory_engine as dme

HITBOX_FLAG = 0x9246BBE6 + 0x24  # = 0x9246BC0A
POLL_HZ     = 240

FLAG_NEUTRAL = 0x0000
FLAG_STARTUP = 0x0018
FLAG_ACTIVE  = 0x0019

# ─────────────────────────────────────────────
# Known typed fields
# offset relative to HITBOX_FLAG
# ─────────────────────────────────────────────

FIELDS = {
    0x00:  ("STATE_FLAG", "u16"),
    +0x28: ("BC02_FLAGS", "u16"),
    +0x40: ("BASE_DAMAGE", "u16"),
    +0x48: ("LAST_ACTIVE_FRAME", "u16"),
    +0x64: ("FIRST_ACTIVE_FRAME", "u16"),
    +0x52: ("PREV_MOVE_LAST_FRAME", "u16"),
    +0x60: ("PREV_MOVE_FIRST_FRAME", "u16"),
    +0x06: ("HITBOX_HEIGHT_TYPE", "f32"),
    -0x56: ("ATTACK_ID", "u16"),
}

# vec3 cluster (12-byte stride)
VEC3_CLUSTER = [
    -0x19A,
    -0x18E,
    -0x182,
    -0x176,
    -0x16A,
    -0x14E,
]

# ─────────────────────────────────────────────

def be_u16(addr):
    return struct.unpack(">H", dme.read_bytes(addr, 2))[0]

def be_f32(addr):
    return struct.unpack(">f", dme.read_bytes(addr, 4))[0]

def read_vec3(addr):
    return (
        be_f32(addr),
        be_f32(addr + 4),
        be_f32(addr + 8)
    )

def dump_snapshot(label):
    print("\n" + "=" * 70)
    print(f"{label} SNAPSHOT")
    print("=" * 70)

    print("\n--- SCALAR FIELDS ---")

    for off, (name, typ) in sorted(FIELDS.items()):
        addr = HITBOX_FLAG + off
        try:
            if typ == "u16":
                val = be_u16(addr)
                print(f"{name:<28} {off:+6d}  0x{val:04X} ({val})")
            elif typ == "f32":
                val = be_f32(addr)
                print(f"{name:<28} {off:+6d}  {val:.6f}")
        except:
            print(f"{name:<28} {off:+6d}  read_error")

    print("\n--- VEC3 CLUSTER ---")
    print(f"{'Index':<6} {'Offset':>8} {'X':>12} {'Y':>12} {'Z':>12}")

    for i, off in enumerate(VEC3_CLUSTER):
        addr = HITBOX_FLAG + off
        try:
            x, y, z = read_vec3(addr)
            print(f"{i:<6} {off:+8d} {x:12.6f} {y:12.6f} {z:12.6f}")
        except:
            print(f"{i:<6} {off:+8d} read_error")

    print()

# ─────────────────────────────────────────────

def monitor_events():

    prev_flag = None

    while True:
        if msvcrt.kbhit():
            if msvcrt.getch().lower() == b'q':
                break

        try:
            flag = be_u16(HITBOX_FLAG)
        except:
            time.sleep(0.1)
            continue

        # Detect transition into STARTUP
        if prev_flag != FLAG_STARTUP and flag == FLAG_STARTUP:
            dump_snapshot("STARTUP (0x0018)")

        # Detect transition into ACTIVE
        if prev_flag != FLAG_ACTIVE and flag == FLAG_ACTIVE:
            dump_snapshot("ACTIVE (0x0019)")

        prev_flag = flag
        time.sleep(1.0 / POLL_HZ)

# ─────────────────────────────────────────────
def debug_flag_stream():
    prev = None
    while True:
        try:
            flag = be_u16(HITBOX_FLAG)
        except:
            time.sleep(0.01)
            continue

        if flag != prev:
            print(f"flag changed → 0x{flag:04X}")
            prev = flag

        time.sleep(1.0 / 500)  # very fast polling
if __name__ == "__main__":
    try:
        dme.hook()
        print("Hooked to Dolphin.")
    except Exception as e:
        sys.exit(f"Hook error: {e}")

    print("Waiting for 0x0018 / 0x0019 transitions.")
    print("Press Q to quit.\n")

    debug_flag_stream()