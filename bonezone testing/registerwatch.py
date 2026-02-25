import struct
import time
import sys

try:
    import dolphin_memory_engine as dme
except ImportError:
    sys.exit("dolphin_memory_engine not installed")

BONE_STRIDE = 0x40

TARGET_BONE = 0x92476140   # aligned base
SCAN_RADIUS = 0x2000       # scan ± around this
POLL_DELAY  = 0.001

def read_block(addr, size):
    return dme.read_bytes(addr, size)

def main():
    print("Hooking...")
    dme.hook()
    print("Connected.")

    print(f"Monitoring bone 0x{TARGET_BONE:08X}")

    last = read_block(TARGET_BONE, BONE_STRIDE)

    while True:
        time.sleep(POLL_DELAY)

        now = read_block(TARGET_BONE, BONE_STRIDE)

        if now != last:
            print("\n[CHANGE DETECTED]")
            print("Scanning surrounding region...")

            region_start = TARGET_BONE - SCAN_RADIUS
            region_end   = TARGET_BONE + SCAN_RADIUS

            changed = []

            for addr in range(region_start, region_end, BONE_STRIDE):
                try:
                    cur = read_block(addr, BONE_STRIDE)
                    if cur != read_block(addr, BONE_STRIDE):
                        changed.append(addr)
                except:
                    pass

            if changed:
                changed.sort()

                print("Detected stride cluster candidates:")
                streak = 1

                for i in range(1, len(changed)):
                    if changed[i] - changed[i-1] == BONE_STRIDE:
                        streak += 1
                        if streak >= 5:
                            base = changed[i] - (streak-1)*BONE_STRIDE
                            print(f"\nLikely bone array base near 0x{base:08X}")
                            return
                    else:
                        streak = 1

            print("No clear cluster found.")
            last = now
        else:
            last = now

if __name__ == "__main__":
    main()