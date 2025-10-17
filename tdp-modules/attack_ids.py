# attack_ids.py
# Read attacker ID fields and map them to generic labels (if CSV present).

import csv
import os
from typing import Optional, Tuple, Dict

from config import ATT_ID_OFF_PRIMARY, ATT_ID_OFF_SECOND, CHAR_AGNOSTIC_CSV
from dolphin_io import rd32


def load_charagnostic_csv(path: str = CHAR_AGNOSTIC_CSV) -> Dict[int, str]:
    """
    Loads a mapping from attacker ID -> generic label.
    Accepts columns:
      - atk_id_dec OR atk_id
      - generic_label OR top_label
    Returns {} if file missing or unreadable.
    """
    mapping: Dict[int, str] = {}
    try:
        if not os.path.exists(path):
            print(f"(Mapping) no mapping CSV found at '{path}' — continuing without mapped names.")
            return mapping

        with open(path, newline="", encoding="utf-8") as fh:
            rdr = csv.DictReader(fh)
            for r in rdr:
                # accept both 'atk_id_dec' and 'atk_id'
                raw = r.get("atk_id_dec") or r.get("atk_id") or ""
                try:
                    aid = int(raw)
                except Exception:
                    continue

                label = (r.get("generic_label") or r.get("top_label") or "").strip()
                if not label:
                    continue
                mapping[aid] = label

        print(f"(Mapping) loaded {len(mapping)} entries from {path}")
    except Exception as e:
        print(f"(Mapping) error loading {path}: {e}")
    return mapping


# Load once on import; you can also call load_charagnostic_csv() again to refresh.
CHAR_AGNOSTIC_MAP: Dict[int, str] = load_charagnostic_csv()


def read_attack_ids(base: Optional[int]) -> Tuple[Optional[int], Optional[int], str]:
    """
    Read attacker ID fields from a fighter base and return:
      (primary_id, secondary_id, mapped_label)

    - primary_id comes from base + ATT_ID_OFF_PRIMARY
    - secondary_id comes from base + ATT_ID_OFF_SECOND
    - mapped_label is looked up in CHAR_AGNOSTIC_MAP using primary_id
    """
    if not base:
        return None, None, ""

    a = rd32(base + ATT_ID_OFF_PRIMARY)
    b = rd32(base + ATT_ID_OFF_SECOND)

    try:
        ai = int(a) if a is not None else None
    except Exception:
        ai = None

    try:
        bi = int(b) if b is not None else None
    except Exception:
        bi = None

    label = CHAR_AGNOSTIC_MAP.get(int(ai)) if isinstance(ai, int) else ""
    return ai, bi, (label or "")
