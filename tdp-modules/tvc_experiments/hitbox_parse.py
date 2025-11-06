# hitbox_parse.py
import struct

def parse_hitbox_block(buf):
    """
    buf: bytes starting at the hitbox block (like 0x908AEFB0)
    must be at least 0x50 bytes
    """
    def f(off):
        return struct.unpack(">f", buf[off:off+4])[0]
    def u(off):
        return struct.unpack(">I", buf[off:off+4])[0]

    out = {}
    out["unknown_A"] = f(0x00)      # usually 0.5
    out["unknown_B"] = f(0x04)      # usually 0.0
    out["unknown_C"] = f(0x08)      # usually 0.5
    out["radius_1"]  = f(0x0C)      # 41 A0 00 00 = 20.0
    out["marker"]    = buf[0x10:0x14]  # expect b"\x35\x0D\x20\x3F"
    out["subid_1"]   = u(0x14)      # 0x0000000A
    out["unknown_F"] = f(0x18)      # 0.5
    out["radius_2"]  = f(0x2C)      # second hitbox
    out["subid_2"]   = u(0x34)      # likely same 0xA
    return out

def looks_like_hitbox(buf):
    return len(buf) >= 0x14 and buf[0x10:0x14] == b"\x35\x0D\x20\x3F"