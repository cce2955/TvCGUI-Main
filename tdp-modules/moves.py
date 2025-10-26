import os, csv
from dolphin_io import rd32
from constants import ATT_ID_OFF_PRIMARY, ATT_ID_OFF_SECOND

# Map display name (what HUD shows in snap["name"]) -> final char_id in CSV.
# Make sure the keys match EXACTLY what your HUD prints for each character.
CHAR_ID_CORRECTION = {
    "Ken the Eagle":     1,
    "Casshan":           2,
    "Tekkaman":          3,
    "Polimar":           4,
    "Yatterman-1":       5,
    "Doronjo":           6,
    "Ippatsuman":        7,
    "Jun the Swan":      8,
    "Karas":             10,
    "Ryu":               12,
    "Chun-Li":           13,
    "Batsu":             14,
    "Morrigan":          15,
    "Alex":              16,
    "Viewtiful Joe":     17,
    "Volnutt":           18,
    "Roll":              19,
    "Saki":              20,
    "Soki":              21,
    "Tekkaman Blade":    26,
    "Joe the Condor":    27,
    "Yatterman-2":       28,
    "Zero":              29,
    "Frank West":        30,
    # Update/add any others, e.g. if HUD shows "Frank" instead of "Frank West",
    # you MUST add "Frank": 30 here.
}


def _pick_label_from_row(row):
    """
    Your CSV rows look like:
      atk_id_dec, atk_id_hex, label1, label2, label3, yes/no, char_id

    We'll choose first non-empty of label1 / label2 / label3.
    """
    for idx in (2, 3, 4):
        if idx < len(row):
            txt = (row[idx] or "").strip()
            if txt:
                return txt
    return ""


def load_move_map(big_csv_path, override_csv_path=None):
    """
    Build nested dict:
        move_map[char_id][atk_id_dec] = "Move Name"

    Source 1: move_id_map_charagnostic.csv (your big file)
    Source 2: move_id_map_charpair.csv (override rows)

    We do NOT assume headers. We parse using csv.reader.
    """
    move_map = {}

    # pass 1: main file
    if os.path.exists(big_csv_path):
        with open(big_csv_path, newline="", encoding="utf-8") as fh:
            rdr = csv.reader(fh)
            for row in rdr:
                if not row:
                    continue

                first_cell = (row[0] or "").strip()
                if not first_cell:
                    continue
                if first_cell.startswith("#"):
                    # e.g. "# Zero (ID: 29)" -> skip
                    continue

                # try to parse move id
                try:
                    atk_id = int(first_cell, 0)  # "315" or "0x13b"
                except Exception:
                    # header-ish row? skip it
                    continue

                # try to parse char_id from last column
                last_cell = (row[-1] or "").strip()
                try:
                    char_id = int(last_cell, 0)
                except Exception:
                    # if last column isn't an int, skip
                    continue

                label = _pick_label_from_row(row)
                if not label:
                    label = f"FLAG_{atk_id}"

                bucket = move_map.setdefault(char_id, {})
                bucket[atk_id] = label

    else:
        print(f"(load_move_map) WARNING: {big_csv_path} not found")

    # pass 2: override file (explicit pairs), if present
    if override_csv_path and os.path.exists(override_csv_path):
        with open(override_csv_path, newline="", encoding="utf-8") as fh:
            rdr = csv.DictReader(fh)
            for r in rdr:
                raw_aid = (r.get("atk_id_dec") or r.get("atk_id_hex") or "").strip()
                raw_cid = (r.get("char_id") or "").strip()
                if not raw_aid or not raw_cid:
                    continue
                try:
                    atk_id = int(raw_aid, 0)
                    char_id = int(raw_cid, 0)
                except Exception:
                    continue

                lab = (
                    (r.get("generic_label") or "") or
                    (r.get("top_label") or "") or
                    (r.get("examples") or "")
                ).strip()
                if not lab:
                    lab = f"FLAG_{atk_id}"

                bucket = move_map.setdefault(char_id, {})
                bucket[atk_id] = lab  # override wins

    # debug: report how many chars and total moves loaded
    total_moves = sum(len(v) for v in move_map.values())
    print(f"(load_move_map) chars:{len(move_map)} total_moves:{total_moves}")

    return move_map


def move_label_for(aid, cid, move_map):
    """
    Lookup rule you just described:
    - Find that character's sub-table by cid.
    - Inside it, find that move ID.
    - If not found, just show FLAG_<aid>.
    """
    if aid is None:
        return "FLAG_NONE"

    # universal special states
    if aid == 48:
        return "BLOCK"
    if aid == 51:
        return "PUSHBLOCK"

    if cid is not None:
        bucket = move_map.get(cid, {})
        if aid in bucket:
            return bucket[aid]

    return f"FLAG_{aid}"


def read_attack_ids(base):
    """
    Read both attack/state IDs from memory.
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
    if val is None:
        return ("?", "UNK")

    if val == 1:   return ("1",   "NEUTRAL")
    if val == 17:  return ("17",  "ATKR_READY")
    if val == 168: return ("168", "DEF_READY")

    if val == 0:   return ("0",   "STARTUP")
    if val == 32:  return ("32",  "STARTUP")
    if val == 6:   return ("6",   "HIT_COMMIT")
    if val == 34:  return ("34",  "CHAIN_BUFFER")
    if val == 36:  return ("36",  "HIT_RESOLVE")
    if val in (37, 5):
        return (str(val), "RECOVERY")

    if val == 4:   return ("4",   "HITSTUN_PUSH")
    if val == 16:  return ("16",  "BLOCK_PUSH")

    if val == 65:  return ("65",  "AIR_CANCEL")
    if val == 64:  return ("64",  "AIR_ASCEND_ATK")
    if val == 192: return ("192", "AIR_DESC_ATK")
    if val == 193: return ("193", "FALLING")
    if val == 70:  return ("70",  "AIR_PREHIT")
    if val == 68:  return ("68",  "AIR_IMPACT")
    if val == 197: return ("197", "KB_GROUNDED")
    if val == 196: return ("196", "KB_VERTICAL")
    if val == 198: return ("198", "KB_VERTICAL_PEAK")
    if val == 96:  return ("96",  "AIR_CHAIN_BUF1")
    if val == 224: return ("224", "AIR_CHAIN_BUF2")
    if val == 230: return ("230",  "AIR_CHAIN_BUF3")
    if val == 194: return ("194",  "AIR_CHAIN_END")

    return (str(val), f"UNK({val})")
