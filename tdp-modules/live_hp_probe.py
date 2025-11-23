# live_hp_probe.py
import time
from dolphin_io import hook, rd32
from constants import SLOTS 

OFF_CUR_HP = 0x28  # this is the field we think is live

def main():
    print("[hp_probe] hooking dolphin…")
    hook()
    print("[hp_probe] hooked. watching slots…")

    # remember last base+hp per slot so we only print diffs
    last_seen = {}

    while True:
        for slot_label, ptr_addr, teamtag in SLOTS:
            # step 1: read the slot pointer → fighter base
            base = rd32(ptr_addr)
            if not base:
                # slot empty
                prev = last_seen.get(slot_label)
                if not prev or prev["base"] != 0:
                    print(f"{slot_label}: (empty)")
                    last_seen[slot_label] = {"base": 0, "hp": None}
                continue

            # step 2: read HP at base + 0x28
            hp_addr = base + OFF_CUR_HP
            hp_val = rd32(hp_addr)

            # step 3: print only on change
            prev = last_seen.get(slot_label)
            if (
                prev is None
                or prev["base"] != base
                or prev["hp"] != hp_val
            ):
                print(
                    f"{slot_label}: base=0x{base:08X}  hp_addr=0x{hp_addr:08X}  hp={hp_val}"
                )
                last_seen[slot_label] = {"base": base, "hp": hp_val}

        time.sleep(0.10)  # 10 times per second is plenty


if __name__ == "__main__":
    main()
