# tvc_hud/mapping.py
import csv, os, sys
from typing import Dict, Tuple, Optional

_HEADER = ['atk_id_dec','atk_id_hex','generic_label','top_label','examples','confirmed','char_id']

def _package_dir() -> str:
    try:
        return os.path.dirname(__file__)
    except Exception:
        return os.getcwd()

def _candidate_paths() -> list:
    names = [
        "move_id_map_charagnostic.csv",   # your file name
        "move_id_map_charagnostic.CSV",
        "move_id_map.csv",                # fallback
    ]
    here = _package_dir()
    wd   = os.getcwd()
    return [os.path.join(here, n) for n in names] + [os.path.join(wd, n) for n in names]

def _to_int_or_none(s: str) -> Optional[int]:
    if s is None: return None
    s = str(s).strip()
    if s == "" or s.upper() == "NONE": return None
    try:
        if s.lower().startswith("0x"):
            return int(s, 16)
        return int(s)
    except Exception:
        # tolerate float-like strings
        try:
            return int(float(s))
        except Exception:
            return None

# “flaggy” ids we don’t treat as concrete moves
_FLAG_IDS = {-1, 0xFFFFFFFF, 4294967295, 1}  # you observed atk_id=1 as airborne/assist flag

class MappingDB:
    """
    Loads CSV into a mapping keyed by (atk_id, char_id) -> label.
    Falls back to (atk_id, None) if per-character not found.
    """
    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._db: Dict[Tuple[int, Optional[int]], str] = {}
        self._loaded = False

    def _resolve_path(self) -> Optional[str]:
        if self.path and os.path.exists(self.path):
            return self.path
        for p in _candidate_paths():
            if os.path.exists(p):
                return p
        return None

    def load(self):
        if self._loaded: return
        p = self._resolve_path()
        if not p:
            # no file; stay empty without crashing
            self._db = {}
            self._loaded = True
            return

        with open(p, newline="", encoding="utf-8") as fh:
            rdr = csv.DictReader(fh)
            for r in rdr:
                aid = _to_int_or_none(r.get('atk_id_dec')) or _to_int_or_none(r.get('atk_id_hex'))
                cid = _to_int_or_none(r.get('char_id'))
                lab = (r.get('generic_label') or r.get('top_label') or r.get('examples') or "").strip()
                if aid is None:      # malformed line
                    continue
                if aid in _FLAG_IDS:  # skip the purely “flag” rows for naming
                    continue
                if not lab:
                    continue
                self._db[(aid, cid)] = lab

        self._loaded = True

    def lookup(self, atk_id: Optional[int], char_id: Optional[int]) -> Optional[str]:
        if not self._loaded: self.load()
        if atk_id is None: return None
        # exact match first
        v = self._db.get((atk_id, char_id))
        if v: return v
        # char-agnostic fallback
        return self._db.get((atk_id, None))

    def is_flag(self, atk_id: Optional[int]) -> bool:
        return (atk_id in _FLAG_IDS) if atk_id is not None else False
