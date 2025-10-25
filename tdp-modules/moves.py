# moves.py
# Attack ID + state decode / move label mapping. :contentReference[oaicite:6]{index=6}

import os, csv
from dolphin_io import rd32
from constants import ATT_ID_OFF_PRIMARY, ATT_ID_OFF_SECOND

def load_generic_map(path):
    """
    Returns {atk_id_dec:int -> label:str}
    """
    mp = {}
    if not os.path.exists(path):
        print("(Map) no", path)
        return mp
    try:
        with open(path, newline='', encoding='utf-8') as fh:
            rdr = csv.reader(fh)
            for row in rdr:
                if not row or row[0].startswith("#"):
                    continue
                try:
                    aid = int(row[0])
                except Exception:
                    continue
                if len(row) >= 3 and row[2].strip():
                    mp[aid] = row[2].strip()
                else:
                    mp[aid] = f"FLAG_{aid}"
    except Exception as e:
        print("(Map) err:", e)
    print(f"(Map) loaded {len(mp)} char-agnostic labels")
    return mp

def load_pair_map(path):
    """
    Returns {(atk_id_dec:int, char_id:int) -> label:str}
    """
    mp = {}
    if not os.path.exists(path):
        print("(MapPairs) no", path, ", continuing.")
        return mp
    try:
        with open(path, newline='', encoding='utf-8') as fh:
            rdr = csv.DictReader(fh)
            for r in rdr:
                try:
                    # supports atk_id_dec or atk_id_hex
                    raw_aid = r.get('atk_id_dec') or r.get('atk_id_hex')
                    aid = int(raw_aid, 0)
                    cid = int(r.get('char_id'))
                except Exception:
                    continue
                lab = (
                    r.get('generic_label')
                    or r.get('top_label')
                    or r.get('examples')
                    or ""
                ).strip()
                if not lab:
                    lab = f"FLAG_{aid}"
                mp[(aid, cid)] = lab
    except Exception as e:
        print("(MapPairs) err:", e)
    print(f"(MapPairs) loaded {len(mp)} exact labels")
    return mp

def move_label_for(aid, cid, pair_map, generic_map):
    if aid == 48:
        return "BLOCK"
    if aid == 51:
        return "PUSHBLOCK"
    if aid is None:
        return "FLAG_NONE"
    if cid is not None and (aid, cid) in pair_map:
        return pair_map[(aid, cid)]
    if aid in generic_map:
        return generic_map[aid]
    return f"FLAG_{aid}"

def read_attack_ids(base):
    """
    Return (attacker_primary_id, attacker_secondary_id) from a fighter base.
    """
    if not base:
        return (None, None)
    a = rd32(base + ATT_ID_OFF_PRIMARY)
    b = rd32(base + ATT_ID_OFF_SECOND)
    try:
        a = int(a) if a is not None else None
    except Exception:
        a = None
    try:
        b = int(b) if b is not None else None
    except Exception:
        b = None
    return a, b

def decode_flag_062(val):
    # returns (rawstr, description)
    if val is None:
        return ("?", "UNK")
    if val == 160: return ("160", "IDLE_BASE")
    if val == 168: return ("168", "ENGAGED")
    if val == 32:  return ("32",  "ACTIVE_MOVE")
    if val == 0:   return ("0",   "ATTACK_ACTIVE")
    if val == 40:  return ("40",  "IMPACTED")
    if val == 8:   return ("8",   "STUN_LOCK")
    return (str(val), f"UNK({val})")

def decode_flag_063(val):
    # condensed from main.py. State machine guesses. Returns (rawstr, description).
    if val is None:
        return ("?", "UNK")

    # neutral / regained control
    if val == 1:
        return ("1", "NEUTRAL")
    if val == 17:
        return ("17", "ATKR_READY")
    if val == 168:
        return ("168", "DEF_READY")

    # grounded attack flow
    if val == 0:
        return ("0", "STARTUP")
    if val == 32:
        return ("32", "STARTUP")
    if val == 6:
        return ("6", "HIT_COMMIT")
    if val == 34:
        return ("34", "CHAIN_BUFFER")
    if val == 36:
        return ("36", "HIT_RESOLVE")
    if val in (37, 5):
        return (str(val), "RECOVERY")

    # victim stun / pushback
    if val == 4:
        return ("4", "HITSTUN_PUSH")
    if val == 16:
        return ("16", "BLOCK_PUSH")

    # aerial / cancels
    if val == 65:
        return ("65", "AIR_CANCEL")
    if val == 64:
        return ("64", "AIR_ASCEND_ATK")
    if val == 192:
        return ("192", "AIR_DESC_ATK")
    if val == 193:
        return ("193", "FALLING")
    if val == 70:
        return ("70", "AIR_PREHIT")
    if val == 68:
        return ("68", "AIR_IMPACT")
    if val == 197:
        return ("197", "KB_GROUNDED")
    if val == 196:
        return ("196", "KB_VERTICAL")
    if val == 198:
        return ("198", "KB_VERTICAL_PEAK")
    if val == 96:
        return ("96", "AIR_CHAIN_BUF1")
    if val == 224:
        return ("224", "AIR_CHAIN_BUF2")
    if val == 230:
        return ("230", "AIR_CHAIN_BUF3")
    if val == 194:
        return ("194", "AIR_CHAIN_END")

    return (str(val), f"UNK({val})")
