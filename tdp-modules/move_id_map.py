# move_id_map.py
#
# Loads move_id_map_charagnostic.csv and exposes a simple lookup:
#   lookup_move_name(anim_id, char_id=None) -> "5A" / "j.C" / etc.
#
# CSV format :
#   0: decimal move ID (canonical)
#   1: hex move ID (often wrong; ignored)
#   2: move name
#   3–5: legacy columns (ignored)
#   6: character ID (100 = global/generic)

import os
import csv

# (char_id -> {anim_id -> name})
_MOVE_NAMES_BY_CHAR = {}
# generic/global normals, char_id == 100
_MOVE_NAMES_GENERIC = {}

_LOADED = False


def _find_csv_path():
    """
    Try a couple of sane default locations.
    Adjust this if you want it somewhere else.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "move_id_map_charagnostic.csv"),
        os.path.join(here, "data", "move_id_map_charagnostic.csv"),
        os.path.join(here, "..", "move_id_map_charagnostic.csv"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _load_if_needed():
    global _LOADED
    if _LOADED:
        return

    csv_path = _find_csv_path()
    if not csv_path:
        print("move_id_map: CSV not found; move labels will fall back.")
        _LOADED = True
        return

    by_char = {}
    generic = {}

    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                # allow comment lines starting with '#'
                if not row:
                    continue
                first = row[0].strip()
                if not first or first.startswith("#"):
                    continue

                # need at least: id_dec, (hex), name, ... , char_id
                if len(row) < 7:
                    continue

                try:
                    anim_id_dec = int(first)
                except ValueError:
                    continue

                name = row[2].strip()
                if not name:
                    continue

                try:
                    # char id column can be "100" or "100.0" depending on how it was saved
                    char_id = int(float(row[6]))
                except ValueError:
                    char_id = 100

                if char_id == 100:
                    # global / generic
                    if anim_id_dec not in generic:
                        generic[anim_id_dec] = name
                else:
                    m = by_char.setdefault(char_id, {})
                    if anim_id_dec not in m:
                        m[anim_id_dec] = name
    except Exception as e:
        print(f"move_id_map: failed to load CSV: {e}")

    _MOVE_NAMES_BY_CHAR.clear()
    _MOVE_NAMES_BY_CHAR.update(by_char)
    _MOVE_NAMES_GENERIC.clear()
    _MOVE_NAMES_GENERIC.update(generic)
    _LOADED = True

    print(
        "move_id_map: loaded "
        f"{sum(len(v) for v in _MOVE_NAMES_BY_CHAR.values())} char-specific and "
        f"{len(_MOVE_NAMES_GENERIC)} generic move IDs from {csv_path}"
    )


def lookup_move_name(anim_id, char_id=None):
    """
    Look up a human-readable move name from the ID map.

    Priority:
      1) exact (char_id, anim_id) match
      2) generic (100, anim_id) match
      3) None  -> caller falls back to old labeling
    """
    if anim_id is None:
        return None

    _load_if_needed()

    if char_id is not None:
        per_char = _MOVE_NAMES_BY_CHAR.get(char_id)
        if per_char:
            name = per_char.get(anim_id)
            if name:
                return name

    return _MOVE_NAMES_GENERIC.get(anim_id)
