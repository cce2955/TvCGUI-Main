# combo_scale_watch.py
import time, struct
import dolphin_memory_engine as dme

BASE = 0x9246B9C0
HIT_TIMER = BASE + 0x11D7   # u16 BE, Q8.8
SCALER    = BASE + 0x11D9   # f32 BE

def ru16_be(a):
    b = dme.read_bytes(a-1, 2)  # timer spans a-1..a (CB97..CB98), keep as-is if you prefer exact start
    return struct.unpack(">H", b)[0] if b and len(b)==2 else None

def rf32_be(a):
    b = dme.read_bytes(a, 4)
    return struct.unpack(">f", b)[0] if b and len(b)==4 else None

dme.hook()
print("time\tQ8.8_frames\t scaler")
while True:
    t = time.strftime("%H:%M:%S")
    raw = ru16_be(HIT_TIMER)
    frames = (raw/256.0) if raw is not None else None
    s = rf32_be(SCALER)
    print(f"{t}\t{frames!s}\t{s!s}")
    time.sleep(1/30)
