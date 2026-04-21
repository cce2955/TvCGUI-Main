# portraits.py
#
# Helpers for loading and resolving character portraits for the HUD.

import os
import pygame


def _normalize_char_key(s: str) -> str:
    """Normalize a character name into a key usable for lookups."""
    s = s.strip().lower()
    for ch in (" ", "-", "_", "."):
        s = s.replace(ch, "")
    return s


# If your asset filenames don't match CHAR_NAMES exactly, you can map
# normalized name -> normalized alias here, e.g.:
#
#   "viewtifuljoe" -> "joe"
#
PORTRAIT_ALIASES = {
    # example:
    # "ptx40a": "ptx",
}


def load_portrait_placeholder() -> pygame.Surface:
    """
    Fallback portrait if a character portrait is missing.

    Tries assets/portraits/placeholder.png first; if that fails,
    creates a simple grey box.
    """
    path = os.path.join("assets", "portraits", "placeholder.png")
    if os.path.exists(path):
        try:
            return pygame.image.load(path).convert_alpha()
        except Exception:
            pass

    surf = pygame.Surface((64, 64), pygame.SRCALPHA)
    surf.fill((80, 80, 80, 255))
    pygame.draw.rect(surf, (140, 140, 140, 255), surf.get_rect(), 2)
    return surf


def load_portraits_from_dir(dirpath: str) -> dict:
    """
    Load all PNG portraits from a directory into a dict keyed by a
    normalized, alias-resolved character name.
    """
    portraits = {}
    if not os.path.isdir(dirpath):
        return portraits

    for fname in os.listdir(dirpath):
        if not fname.lower().endswith(".png"):
            continue

        full = os.path.join(dirpath, fname)
        stem = os.path.splitext(fname)[0]
        key = _normalize_char_key(stem)

        try:
            img = pygame.image.load(full).convert_alpha()
            portraits[key] = img
        except Exception as e:
            print("portrait load failed for", full, e)

    return portraits


def get_portrait_for_snap(snap: dict, portraits: dict, placeholder: pygame.Surface):
    """
    Resolve the correct portrait Surface for a fighter snapshot.

    snap: fighter snapshot dict (must contain 'name' if known)
    portraits: dict from normalized name -> Surface
    placeholder: fallback Surface
    """
    if not snap:
        return None

    cname = snap.get("name")
    if not cname:
        return placeholder

    norm = _normalize_char_key(cname)
    norm = PORTRAIT_ALIASES.get(norm, norm)
    return portraits.get(norm, placeholder)
