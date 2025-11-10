import os, csv
from dolphin_io import rd32
from constants import ATT_ID_OFF_PRIMARY, ATT_ID_OFF_SECOND

# VERY IMPORTANT:
# Keys here MUST exactly match snap["name"] as shown in your HUD.
# Values MUST match the final column in your CSV for that character.
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

    # If the HUD ever prints slightly different names (like "Frank" or "ZERO"),
    # you MUST add those spellings here mapped to the same number.
    # e.g. "ZERO": 29,
    #      "Frank": 30,
}


def _pick_label_from_row(row):
    """
    Your CSV rows look like:
      atk_id_dec, atk_id_hex, label1, label2, label3, yes/no, char_id

    We'll choose the first non-empty of label1 / label2 / label3.
    """
    for idx in (2, 3, 4):
        if idx < len(row):
            txt = (row[idx] or "").strip()
            if txt:
                return txt
    return ""


def load_move_map(big_csv_path, override_csv_path=None):
    """
    Build TWO maps:

      move_map[char_id][atk_id]  = "Move Name"
      global_map[atk_id]         = "Move Name"   (for char_id == 100)

    We parse both CSVs:
    - move_id_map_charagnostic.csv  (main "big" file)
    - move_id_map_charpair.csv      (override / refinement)
    """
    move_map   = {}  # dict[int -> dict[int -> str]]
    global_map = {}  # dict[int -> str]

    # PASS 1: bulk file (the "agnostic" file that actually has per-char rows)
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
                    # e.g. "# Zero (ID: 29)"
                    continue

                # move id
                try:
                    atk_id = int(first_cell, 0)  # handles "315" or "0x13b"
                except Exception:
                    # header-ish or junk row
                    continue

                # char id = last col
                last_cell = (row[-1] or "").strip()
                try:
                    char_id = int(last_cell, 0)
                except Exception:
                    # can't map without char_id
                    continue

                label = _pick_label_from_row(row)
                if not label:
                    label = f"FLAG_{atk_id}"

                if char_id == 100:
                    # This is your global/system/assist/etc bucket.
                    global_map[atk_id] = label
                else:
                    bucket = move_map.setdefault(char_id, {})
                    bucket[atk_id] = label
    else:
        print(f"(load_move_map) WARNING: {big_csv_path} not found")

    # PASS 2: overrides file (pair csv)
    # This lets you hand-fix specific (char_id, atk_id) names or add new ones.
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

                if char_id == 100:
                    global_map[atk_id] = lab
                else:
                    bucket = move_map.setdefault(char_id, {})
                    bucket[atk_id] = lab

    total_chars  = len(move_map)
    total_moves  = sum(len(v) for v in move_map.values())
    total_global = len(global_map)
    print(f"(load_move_map) chars:{total_chars} char_moves:{total_moves} global_flags:{total_global}")

    return move_map, global_map


def move_label_for(aid, cid, move_map, global_map):
    """
    Priority:
      1. If we know cid (the character), try that character's table: move_map[cid][aid]
      2. Else try global_map[aid] (shared/system flags / throws / movement states)
      3. Else fall back to FLAG_<aid>
    """
    if aid is None:
        return "FLAG_NONE"

    # universal stuff you already recognized
    if aid == 48:
        return "BLOCK"
    if aid == 51:
        return "PUSHBLOCK"

    if cid is not None:
        bucket = move_map.get(cid)
        if bucket and aid in bucket:
            return bucket[aid]

    if aid in global_map:
        return global_map[aid]

    return f"FLAG_{aid}"


def read_attack_ids(base):
    """
    Pull raw state/move IDs from memory.
    Returns (attA, attB)
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
    if val == 32:  return ("32",  "MOVEMENT")
    if val == 0:   return ("0",   "ATTACK_ACTIVE")
    if val == 40:  return ("40",  "IMPACTED")
    if val == 8:   return ("8",   "STUN_LOCK")
    if val == 136:   return ("136",   "ATK_END")
    if val == 128:   return ("128",   "ATK_REC")
    if val == 48:   return ("48",   "THROW_TECH")
    if val == 16:   return ("16",   "THROW")
    if val == 64:   return ("64",   "??? Only appears on throw knockdown")
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
