# live_capture.py
import time
import json
from hitbox_parse import parse_hitbox_block, looks_like_hitbox

# CONFIG: tune these
P1_MOVE_ADDR   = 0x908AEFB0  # current known runtime block for P1
BLOCK_READSIZE = 0x80        # read a little extra

def read_mem(addr, size):
    """
    Replace with your Dolphin/tvc_sigtool read.
    Should return `size` bytes.
    """
    raise NotImplementedError

def capture_one(player, char_id, attack_id, move_addr=P1_MOVE_ADDR):
    raw = read_mem(move_addr, BLOCK_READSIZE)
    # first 0x50 is the known layout
    block = raw[:0x50]
    rec = {
        "ts": time.time(),
        "player": player,
        "char_id": char_id,
        "attack_id": attack_id,
        "move_addr": hex(move_addr),
        "raw": raw.hex()
    }
    if looks_like_hitbox(block):
        rec["parsed"] = parse_hitbox_block(block)
    else:
        rec["parsed"] = None
    # append to a log file
    with open("hitbox_capture_log.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    print("captured", rec["player"], rec["char_id"], rec["attack_id"], "at", rec["move_addr"])
    return rec

# example hook you call from your existing HUD/event code:
def on_attack_start(player, char_id, attack_id):
    if player == 1:
        capture_one(player, char_id, attack_id, P1_MOVE_ADDR)
    else:
        # later: find P2 equivalent addr
        pass