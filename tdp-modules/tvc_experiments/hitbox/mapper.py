# mapper.py
import time
import json
from hitbox_parse import parse_hitbox_block, looks_like_hitbox

P1_MOVE_ADDR   = 0x908AEFB0
BLOCK_READSIZE = 0x80

def read_mem(addr, size):
    raise NotImplementedError

def get_current_attack_id_for_p1():
    raise NotImplementedError

def get_current_char_id_for_p1():
    """
    Same as above, read from your existing char_id source.
    """
    raise NotImplementedError

def snapshot_move(player, char_id, attack_id):
    raw = read_mem(P1_MOVE_ADDR, BLOCK_READSIZE)
    block = raw[:0x50]
    rec = {
        "ts": time.time(),
        "player": player,
        "char_id": char_id,
        "attack_id": attack_id,
        "move_addr": hex(P1_MOVE_ADDR),
        "raw": raw.hex()
    }
    if looks_like_hitbox(block):
        rec["parsed"] = parse_hitbox_block(block)
    else:
        rec["parsed"] = None
    return rec

def mapper_loop():
    seen = set()  # (char_id, attack_id)
    while True:
        char_id   = get_current_char_id_for_p1()
        attack_id = get_current_attack_id_for_p1()
        if attack_id != 0 and (char_id, attack_id) not in seen:
            rec = snapshot_move(1, char_id, attack_id)
            with open("hitbox_map.jsonl", "a") as f:
                f.write(json.dumps(rec) + "\n")
            seen.add((char_id, attack_id))
            print("mapped", char_id, attack_id)
        time.sleep(0.016)  # ~60fps poll