import sys
sys.path.insert(0, '/mnt/data/win_counter_src')
from fd_unknowns import extract_unmapped_field_ops, scan_unmapped_field_ops

BASE=0x90800000
buf=bytearray(0x100)
# Packet: 04 15 60 00 [target +0x5C] [type 3F000000] [value 0x00000008]
packet=bytes.fromhex('04 15 60 00 00 00 00 5C 3F 00 00 00 00 00 00 08')
buf[0x20:0x20+len(packet)]=packet
# Repeated example to make sure repeats are retained.
buf[0x60:0x60+len(packet)]=packet

def read(addr,n):
    off=addr-BASE
    return bytes(buf[off:off+n])

move={'abs':BASE,'id':0x161,'move_name':'Tatsu Super'}
rows=extract_unmapped_field_ops(move, read, next_abs_map={BASE:BASE+0x100})
assert len(rows)==2, len(rows)
assert rows[0].signature.target_offset==0x5C
assert rows[0].signature.subop==0x15
assert rows[0].signature.marker==0x60
assert rows[0].signature.value_raw==8
allrows,scanned=scan_unmapped_field_ops([move,move],read,next_abs_map={BASE:BASE+0x100})
assert scanned==1, scanned
assert len(allrows)==2, len(allrows)
print('fd_unknowns synthetic scanner: PASS')
