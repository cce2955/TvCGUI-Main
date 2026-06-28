# fd_move_families.py
#
# Display-only helpers for linking move-table sections that are clearly part of
# one human move.  This does not write memory and does not change scanner output;
# it only annotates rows so fd_tree can present command wrappers, internal
# start/spin/end sections, and repeated A/B/C or L/M/H variants as one readable
# family.

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _text(v: Any) -> str:
    return "" if v is None else str(v)


def _safe_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _text(text).lower()).strip("_") or "group"


def _clean_label(text: str) -> str:
    s = _text(text).strip()
    s = re.sub(r"\s+", " ", s)
    # Display family/link labels should not inherit scouting uncertainty
    # punctuation from the map.  The raw Move column can still show the original
    # name; the Link column is the organized view.
    s = s.replace("?", "")
    return s.strip()


def _move_name(mv: Dict[str, Any]) -> str:
    for key in ("move_name", "pretty_name", "name"):
        s = _text(mv.get(key)).strip()
        if s:
            return s
    aid = mv.get("id")
    return f"anim_{int(aid):04X}" if aid is not None else ""


def _strength_from_name(name: str) -> Optional[str]:
    s = _clean_label(name)
    m = re.search(r"(?:^|\s)([LMHABC])(?:\s|$)", s, flags=re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return None


def _context_from_name(name: str) -> str:
    low = _text(name).lower()
    if "assist" in low:
        return "Assist"
    if low.startswith("air ") or " air " in low or low.endswith(" air") or low.startswith("j.") or "jump" in low:
        return "Air"
    return "Ground"


def _phase_from_name(name: str) -> Optional[str]:
    low = _text(name).lower()
    checks = (
        ("startup", "Startup"),
        ("windup", "Windup"),
        ("start", "Start"),
        ("spin", "Spin"),
        ("end", "End"),
        ("second phase", "Second phase"),
        ("second hit", "Hit 2"),
        ("second", "Second"),
        ("follow", "Followup"),
        ("entry", "Entry"),
    )
    for needle, label in checks:
        if needle in low:
            return label
    return None


# Ryu is the first character where this became obvious enough to deserve exact
# ID hints: the scanner sees internal Tatsu sections such as Start/Spin/End as
# separate records, while the player-facing command is still Tatsu L/M/H or Air
# Tatsu L/M/H.  These are still display-only.
_RYU_TATSU_SECTION_PHASE = {
    273: "Start",
    274: "Spin",
    275: "End",
}
_RYU_TATSU_ENTRY = {
    307: ("Ground", "L"),
    308: ("Ground", "M"),
    309: ("Ground", "H"),
    323: ("Air", "L"),
    324: ("Air", "M"),
    325: ("Air", "H"),
}
_RYU_TATSU_SUPER = {279: "Startup", 280: "Windup", 353: "Entry", 354: "Air entry"}

_GENERIC_FAMILY_WORDS = (
    ("tatsu super", "Tatsu Super"),
    ("tatsu", "Tatsu"),
    ("hado", "Hado"),
    ("shoryu", "Shoryu"),
    ("donkey", "Donkey"),
)

_NOISE_WORDS = (
    "throw",
    "thrown",
    "filler",
    "idle",
    "landing",
    "land",
    "walk",
    "jump",
    "dash",
    "backdash",
    "stand",
    "crouch",
    "block",
    "hitstun",
    "damage",
    "ko",
    "recovery",
    "knockdown",
    "launched",
    "spiral",
    "pushblock",
    "push block",
    "baroque",
    "time over",
    "turn around",
    "puddle",
    "slip",
    "swept",
    "stun",
    "victory",
    "win",
    "lose",
    "match start",
    "assist",
    "taunt",
    "tag in",
    "bounce",
    "sweep reaction",
    "sweep",
    "snapback",
    "wakeup",
    "get up",
    "roll",
    "crumple",
    "stagger",
    "tpose",
    "t-pose",
    "parry",
)
_NORMAL_NOTATION_RE = re.compile(r"^(?:[1-9][abc]|j\.?[abc]|j\.?2c|[abc])$", re.IGNORECASE)
_PHASE_ORDER = {
    "Startup": 0,
    "Windup": 1,
    "Start": 2,
    "Spin": 3,
    "Followup": 4,
    "Second phase": 5,
    "Second": 5,
    "Hit 2": 6,
    "End": 7,
    "Entry": 8,
    "Air entry": 8,
}
_GROUND_GUESSES = {1: "L", 2: "M", 3: "H"}
_STRENGTH_ORDER_LMH = ("L", "M", "H")
_STRENGTH_ORDER_ABC = ("A", "B", "C")



def _named_strengths(members: Iterable[Dict[str, Any]]) -> List[str]:
    seen: List[str] = []
    for mv in members or []:
        st = _clean_label(mv.get("family_strength") or mv.get("family_strength_guess") or "")
        if st in _STRENGTH_ORDER_LMH or st in _STRENGTH_ORDER_ABC:
            if st not in seen:
                seen.append(st)
    return seen


def _strength_order_for_family(label: str, members: Iterable[Dict[str, Any]] = ()) -> Tuple[str, ...]:
    "Pick the strength language for a family.\n\n    TvC's map is mixed: Ryu style specials are usually L/M/H, but a lot of the\n    cast is named A/B/C.  Use explicit names when the module has them.  When an\n    internal chain is unnamed, fall back to L/M/H because that matches the\n    command-strength mental model the frame editor is trying to expose.\n    "
    strengths = _named_strengths(members)
    has_lmh = any(st in _STRENGTH_ORDER_LMH for st in strengths)
    has_abc = any(st in _STRENGTH_ORDER_ABC for st in strengths)
    if has_lmh and not has_abc:
        return _STRENGTH_ORDER_LMH
    if has_abc and not has_lmh:
        return _STRENGTH_ORDER_ABC

    low = _clean_label(label).lower()
    if any(word in low for word in ("tatsu", "hado", "shoryu", "sbk")):
        return _STRENGTH_ORDER_LMH
    return _STRENGTH_ORDER_LMH


def _infer_chain_strength(label: str, chain_index: int, members: Iterable[Dict[str, Any]]) -> str:
    # Existing explicit strength inside the chain is stronger than any address
    # order guess.
    strengths = _named_strengths(members)
    if strengths:
        return strengths[0]
    order = _strength_order_for_family(label, members)
    if 1 <= int(chain_index or 0) <= len(order):
        return order[int(chain_index) - 1]
    return ""


def _is_named_lookup_row(mv: Dict[str, Any]) -> bool:
    name = _clean_label(_move_name(mv))
    low = name.lower()
    if not name or low.startswith("anim_") or "filler" in low:
        return False
    return _text(mv.get("move_name_source")).lower() == "lookup"


def _can_absorb_unnamed_row(mv: Dict[str, Any]) -> bool:
    if mv.get("family_linkable"):
        return False
    kind = _base_kind(mv)
    if kind not in {"special", "super"}:
        return False
    name = _clean_label(_move_name(mv))
    low = name.lower()
    if "assist" in low or "throw" in low or "thrown" in low or "taunt" in low:
        return False
    # Only absorb unlabeled scout rows.  Named specials should stand on their
    # own unless the normal classifier has already put them in a family.
    return low.startswith("anim_") or "filler" in low or _text(mv.get("move_name_source")).lower() in {"anim", "anim_map", "none"}


def _normal_command_name(name: str) -> bool:
    low = _clean_label(name).lower().replace(" ", "")
    return low in {"5a", "2a", "5b", "2b", "6b", "5c", "2c", "6c", "4c", "3c", "j.a", "ja", "j.b", "jb", "j.c", "jc", "j.2c", "j2c"}


def _has_meaningful_hit_segments(mv: Dict[str, Any]) -> bool:
    segs = mv.get("hit_segments") or []
    if not isinstance(segs, list) or len(segs) <= 1:
        return False
    for seg in segs:
        try:
            if int(seg.get("damage") or 0) != 0:
                return True
        except Exception:
            pass
    return False


def _is_multihit_helper_row(mv: Dict[str, Any]) -> bool:
    if mv.get("family_linkable"):
        return False
    if not _has_meaningful_hit_segments(mv):
        return False
    name = _clean_label(_move_name(mv)).lower()
    src = _text(mv.get("move_name_source")).lower()
    if name.startswith("anim_") or name == "anim_--":
        return True
    return src in {"anim", "anim_map", "none"}


def _is_multihit_owner_candidate(mv: Dict[str, Any]) -> bool:
    if _is_multihit_helper_row(mv):
        return False
    # Rows that already own their own per-hit bundles do not need an unnamed
    # duplicate helper grafted onto them.  The helper is usually the same script
    # seen through another anchor.
    if _has_meaningful_hit_segments(mv):
        return False
    name = _clean_label(_move_name(mv))
    low = name.lower()
    if not name or low.startswith("anim_"):
        return False
    if any(word in low for word in ("ko", "knockdown", "landing", "jump", "cancel", "assist", "forcefield", "throw", "thrown", "taunt", "second", "???")):
        return False
    if "()" in low:
        return False
    try:
        aid_i = int(mv.get("id")) if mv.get("id") is not None else None
    except Exception:
        aid_i = None
    if aid_i is not None and aid_i < 0x100:
        return False
    if mv.get("family_linkable"):
        return True
    if _normal_command_name(name):
        return True
    return _is_named_lookup_row(mv)



def _same_segment_damage_addrs(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    def addrs(mv: Dict[str, Any]) -> List[int]:
        out: List[int] = []
        for seg in mv.get("hit_segments") or []:
            try:
                addr = int(seg.get("damage_addr") or 0)
            except Exception:
                addr = 0
            if addr:
                out.append(addr)
        return out
    aa = addrs(a)
    bb = addrs(b)
    return bool(aa and bb and aa == bb)


def _helper_duplicates_existing_multihit(helper: Dict[str, Any], moves: List[Dict[str, Any]]) -> bool:
    for mv in moves:
        if mv is helper:
            continue
        if _is_multihit_helper_row(mv):
            continue
        if not _has_meaningful_hit_segments(mv):
            continue
        if _same_segment_damage_addrs(helper, mv):
            return True
    return False

def _segment_addrs(mv: Dict[str, Any]) -> List[int]:
    out: List[int] = []
    for seg in mv.get("hit_segments") or []:
        for key in ("active_addr", "damage_addr", "atkprop_addr", "knockback_addr", "stun_addr"):
            try:
                a = int(seg.get(key) or 0)
            except Exception:
                a = 0
            if a:
                out.append(a)
    return out


def _attach_multihit_helper_rows_to_nearby_named_rows(moves: List[Dict[str, Any]], char_name: Optional[str]) -> None:
    """Display-only ownership for unnamed multi-hit helper scripts.

    Many TvC commands are wrappers: the named row has startup/control state,
    while actual active/damage packets live in a nearby unnamed helper script.
    This pass keeps those helpers near the command a player recognizes, without
    changing any write address.
    """
    helpers = [mv for mv in moves if _is_multihit_helper_row(mv) and not _helper_duplicates_existing_multihit(mv, moves)]
    owners = [mv for mv in moves if _is_multihit_owner_candidate(mv) and _family_addr(mv)]
    if not helpers or not owners:
        return

    ckey = _safe_key(char_name or "char")

    for helper in helpers:
        haddr = _family_addr(helper)
        if not haddr:
            continue
        hpoints = [haddr] + _segment_addrs(helper)
        best: Optional[Tuple[int, Dict[str, Any]]] = None

        for owner in owners:
            oaddr = _family_addr(owner)
            if not oaddr:
                continue
            # Score against both the helper script anchor and the real hit
            # packet addresses.  Some wrappers sit just before the helper
            # root, while others sit beside one of the internal hit packets.
            gaps = [abs(oaddr - hp) for hp in hpoints if hp]
            if not gaps:
                continue
            gap = min(gaps)

            # Normal/wrapper rows are allowed to reach a little farther because
            # the helper root can sit after a protected startup script.
            max_gap = 0x1C00 if _normal_command_name(_move_name(owner)) else 0x1400
            if owner.get("family_linkable"):
                max_gap = max(max_gap, 0x1800)
            if gap > max_gap:
                continue

            # Prefer a wrapper before the helper, then named/family rows, then
            # the shortest physical gap.
            score = gap
            if oaddr > haddr:
                score += 0x280
            if owner.get("family_linkable"):
                score -= 0x120
            if _is_named_lookup_row(owner):
                score -= 0x80
            if owner.get("damage") is None and owner.get("active_start") is None:
                score -= 0x60
            if best is None or score < best[0]:
                best = (score, owner)

        if not best:
            continue

        owner = best[1]
        owner_name = _clean_label(_move_name(owner)) or "Linked move"
        base_label = _clean_label(owner.get("family_label") or owner_name)
        if not base_label or base_label.lower().startswith("anim_"):
            base_label = owner_name

        if owner.get("family_linkable"):
            gkey = str(owner.get("family_group_key") or owner.get("family_key") or f"{ckey}:{_safe_key(base_label)}")
            glabel = _clean_label(owner.get("family_group_label") or owner.get("family_label") or base_label)
            fkey = str(owner.get("family_key") or gkey)
            chain_idx = owner.get("family_chain_index")
            chain_label = _clean_label(owner.get("family_chain_label") or "")
            strength = _clean_label(owner.get("family_strength") or owner.get("family_strength_guess") or "")
            context = _clean_label(owner.get("family_context") or "")
        else:
            gkey = f"{ckey}:{_safe_key(owner_name)}:linked_hits"
            glabel = f"{owner_name} linked sections"
            fkey = gkey
            chain_idx = None
            chain_label = ""
            strength = ""
            context = _context_from_name(owner_name)
            owner["family_key"] = fkey
            owner["family_label"] = owner_name
            owner["family_role"] = "entry"
            owner["family_phase"] = "Entry"
            owner["family_context"] = context
            owner["family_strength"] = ""
            owner["family_link_label"] = f"{owner_name} / Entry"
            owner["family_group_key"] = gkey
            owner["family_group_label"] = glabel
            owner["family_confidence"] = "nearby-multihit-owner"
            owner["family_linkable"] = True

        helper["family_key"] = fkey
        helper["family_label"] = base_label
        helper["family_role"] = "section"
        helper["family_phase"] = "Linked hits"
        helper["family_context"] = context
        helper["family_strength"] = strength
        helper["family_linkable"] = True
        helper["family_group_key"] = gkey
        helper["family_group_label"] = glabel
        if chain_idx:
            helper["family_chain_index"] = chain_idx
        if chain_label:
            helper["family_chain_label"] = chain_label
        bits = [base_label]
        if chain_label:
            bits.append(chain_label)
        else:
            if context and context != "Section":
                bits.append(context)
            if strength:
                bits.append(strength)
        bits.append("Linked hits")
        helper["family_link_label"] = " / ".join([b for b in bits if b])
        helper["family_confidence"] = "nearby-multihit-helper"
        try:
            helper["linked_owner_abs"] = int(owner.get("abs") or 0)
            owner.setdefault("linked_helper_abs", int(helper.get("abs") or 0))
        except Exception:
            pass


def _is_ryu(char_name: Optional[str]) -> bool:
    return _text(char_name).strip().lower() == "ryu"


def _base_kind(mv: Dict[str, Any]) -> str:
    return _text(mv.get("kind")).strip().lower()


def _strip_context_words(s: str) -> Tuple[str, str]:
    context = _context_from_name(s)
    out = _clean_label(s)
    out = re.sub(r"^air\s+", "", out, flags=re.IGNORECASE).strip()
    out = re.sub(r"\s+air$", "", out, flags=re.IGNORECASE).strip()
    out = re.sub(r"^ground\s+", "", out, flags=re.IGNORECASE).strip()
    out = re.sub(r"^assist\s+", "", out, flags=re.IGNORECASE).strip()
    return out, context


def _strip_phase_words(s: str) -> Tuple[str, Optional[str]]:
    out = _clean_label(s)
    phase = _phase_from_name(out)
    if not phase:
        return out, None
    # Remove phase tokens only at natural word boundaries so names like
    # "Ender" are not damaged by the End phase rule.
    replacements = (
        (r"\bsecond\s+phase\b", ""),
        (r"\bsecond\s+hit\b", ""),
        (r"\bstartup\b", ""),
        (r"\bwindup\b", ""),
        (r"\bstart\b", ""),
        (r"\bspin\b", ""),
        (r"\bend\b", ""),
        (r"\bfollow(?:up)?\b", ""),
        (r"\bentry\b", ""),
        (r"\bsecond\b", ""),
    )
    for pat, repl in replacements:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    return _clean_label(out), phase


def _strip_strength_words(s: str) -> Tuple[str, Optional[str]]:
    out = _clean_label(s)
    strength: Optional[str] = None

    # Common player-facing suffixes: Hado L, Bird Run A, Tek Lancer B.
    m = re.search(r"(?:^|\s)([LMHABC])$", out, flags=re.IGNORECASE)
    if m:
        strength = m.group(1).upper()
        out = _clean_label(out[:m.start(1)])

    # Some map rows use "A Air" or "B Air".  Context stripping usually removes
    # Air first, but keep this as a safe fallback.
    m = re.search(r"(?:^|\s)([LMHABC])\s+air$", out, flags=re.IGNORECASE)
    if m:
        strength = m.group(1).upper()
        out = _clean_label(out[:m.start(1)])

    return out, strength


def _looks_like_noise_name(name: str, mv: Dict[str, Any]) -> bool:
    s = _clean_label(name)
    low = s.lower()
    compact = low.replace(" ", "").replace(".", "")
    if not s or low.startswith("anim_") or low == "anim_--":
        return True
    if _NORMAL_NOTATION_RE.match(low) or compact in {"5a", "2a", "5b", "2b", "6b", "5c", "2c", "6c", "4c", "3c", "ja", "jb", "jc", "j2c"}:
        return True
    if any(word in low for word in _NOISE_WORDS):
        return True
    return False


def _derive_generic_family(name: str, mv: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    'Infer a cross-character family from the move name.\n\n    This is intentionally name-driven and display-only.  It lets the workbench\n    organize non-Ryu characters without claiming that the known state is every command\n    route.  Exact writes still use the original row addresses.\n    '
    original = _clean_label(name)
    if _looks_like_noise_name(original, mv):
        return None

    try:
        aid_i = int(mv.get("id")) if mv.get("id") is not None else None
    except Exception:
        aid_i = None
    # Generic/system reaction IDs live below the move-script range and should
    # not become giant linked families. Character specials/supers and their
    # helpers are in the 0x100+ range.
    if aid_i is not None and aid_i < 0x100:
        return None

    # Do not turn every random raw special row into a header unless there is a
    # human-readable lookup/map name.
    src = _text(mv.get("move_name_source")).lower()
    if src in {"anim", "anim_map", "none"} and _base_kind(mv) not in {"super"}:
        if not any(word in original.lower() for word, _ in _GENERIC_FAMILY_WORDS):
            return None

    no_ctx, context = _strip_context_words(original)
    no_phase, phase = _strip_phase_words(no_ctx)
    base, strength = _strip_strength_words(no_phase)

    # Phase can appear after the strength in a few internal names; run the phase
    # strip again after removing strength.
    base2, phase2 = _strip_phase_words(base)
    if phase2 and not phase:
        phase = phase2
        base = base2

    base = _clean_label(base)
    if not base or _looks_like_noise_name(base, {**mv, "move_name_source": "lookup"}):
        return None

    # Very short one-token bases like "A" or "B" are not useful families.
    if len(base) <= 1:
        return None

    role = "section" if phase and phase not in {"Entry", "Air entry"} else "entry"
    phase = phase or "Entry"

    return {
        "family_label": base,
        "family_context": context,
        "family_strength": strength or "",
        "family_phase": phase,
        "family_role": role,
        "family_confidence": "name-generic",
        "family_linkable": True,
    }


def classify_move(mv: Dict[str, Any], char_name: Optional[str] = None) -> Dict[str, Any]:
    """Return display-only family metadata for a scanned move row."""
    aid_raw = mv.get("id")
    try:
        aid = int(aid_raw) if aid_raw is not None else None
    except Exception:
        aid = None

    name = _move_name(mv)
    low = name.lower()

    family_label = ""
    family_key = ""
    role = ""
    phase = _phase_from_name(name)
    context = _context_from_name(name)
    strength = _strength_from_name(name)
    confidence = "name"
    linkable = False

    if _is_ryu(char_name):
        if aid in _RYU_TATSU_SUPER or "tatsu super" in low:
            family_label = "Tatsu Super"
            family_key = "ryu:tatsu_super"
            phase = _RYU_TATSU_SUPER.get(aid) or phase or "Section"
            role = "entry" if phase in {"Entry", "Air entry"} else "section"
            linkable = True
            confidence = "id-confirmed" if aid in _RYU_TATSU_SUPER else "name"
        elif aid in _RYU_TATSU_SECTION_PHASE:
            family_label = "Tatsu"
            family_key = "ryu:tatsu"
            phase = _RYU_TATSU_SECTION_PHASE[aid]
            role = "section"
            context = "Section"
            strength = None
            linkable = True
            confidence = "id-confirmed"
        elif aid in _RYU_TATSU_ENTRY:
            family_label = "Tatsu"
            family_key = "ryu:tatsu"
            context, strength = _RYU_TATSU_ENTRY[aid]
            phase = "Entry"
            role = "entry"
            linkable = True
            confidence = "id-confirmed"

    if not family_key:
        generic = _derive_generic_family(name, mv)
        if generic:
            family_label = generic["family_label"]
            context = generic["family_context"]
            strength = generic["family_strength"]
            phase = generic["family_phase"]
            role = generic["family_role"]
            confidence = generic["family_confidence"]
            linkable = bool(generic["family_linkable"])
            ckey = _safe_key(char_name or "char")
            family_key = f"{ckey}:{_safe_key(family_label)}"

    if not family_key:
        for needle, label in _GENERIC_FAMILY_WORDS:
            if needle in low:
                family_label = label
                ckey = _safe_key(char_name or "char")
                family_key = f"{ckey}:{_safe_key(label)}"
                role = "section" if phase else "entry"
                phase = phase or "Entry"
                linkable = True
                break

    if not family_key:
        return {
            "family_key": "",
            "family_label": "",
            "family_role": "",
            "family_phase": "",
            "family_context": "",
            "family_strength": "",
            "family_link_label": "",
            "family_group_key": "",
            "family_group_label": "",
            "family_confidence": "",
            "family_linkable": False,
        }

    family_label = _clean_label(family_label)
    context = _clean_label(context)
    strength = _clean_label(strength or "")
    phase = _clean_label(phase or "")

    bits: List[str] = [family_label]
    if context and context != "Section":
        bits.append(context)
    if strength:
        bits.append(strength)
    if phase:
        bits.append(phase)
    link_label = " / ".join(bits)

    return {
        "family_key": family_key,
        "family_label": family_label,
        "family_role": role,
        "family_phase": phase or "",
        "family_context": context or "",
        "family_strength": strength or "",
        "family_link_label": link_label,
        "family_group_key": family_key,
        "family_group_label": family_label,
        "family_confidence": confidence,
        "family_linkable": bool(linkable),
    }


def _annotate_ryu_tatsu_section_chains(moves: List[Dict[str, Any]]) -> None:
    sections = []
    for mv in moves:
        if mv.get("family_key") == "ryu:tatsu" and mv.get("family_role") == "section":
            try:
                addr = int(mv.get("abs") or 0)
            except Exception:
                addr = 0
            if addr:
                sections.append(mv)

    sections.sort(key=lambda m: int(m.get("abs") or 0))
    if not sections:
        return

    chains: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    last_addr: Optional[int] = None
    last_order = -1

    def close_current() -> None:
        nonlocal current, last_addr, last_order
        if current:
            chains.append(current)
        current = []
        last_addr = None
        last_order = -1

    for mv in sections:
        phase = _text(mv.get("family_phase"))
        order = {"Start": 1, "Spin": 2, "End": 3}.get(phase, 99)
        addr = int(mv.get("abs") or 0)
        gap = (addr - last_addr) if last_addr is not None else 0

        if current and (phase == "Start" or order <= last_order or gap > 0x1800):
            close_current()

        current.append(mv)
        last_addr = addr
        last_order = order

    close_current()

    for idx, chain in enumerate(chains, start=1):
        phases = {m.get("family_phase") for m in chain}
        complete_groundish = {"Start", "Spin", "End"}.issubset(phases)
        strength = _GROUND_GUESSES.get(idx) if complete_groundish else None
        chain_label = f"Chain {idx}"
        if strength:
            chain_label = f"Ground {strength} chain"
        for mv in chain:
            phase = _text(mv.get("family_phase")) or "Section"
            mv["family_chain_index"] = idx
            mv["family_chain_label"] = chain_label
            mv["family_group_key"] = f"ryu:tatsu:{_safe_key(chain_label)}"
            mv["family_group_label"] = f"Tatsu {chain_label}"
            if strength and not mv.get("family_strength"):
                mv["family_strength_guess"] = strength
                mv["family_strength"] = strength
            mv["family_link_label"] = f"Tatsu / {chain_label} / {phase}"
            mv["family_confidence"] = "chain+id-confirmed" if strength else "chain"


def _family_addr(mv: Dict[str, Any]) -> int:
    try:
        return int(mv.get("abs") or 0)
    except Exception:
        return 0


def _attach_nearby_ryu_tatsu_entries_to_chains(moves: List[Dict[str, Any]]) -> None:
    """Keep obvious Tatsu entry wrappers near the linked section they call."""
    chains: Dict[int, Dict[str, Any]] = {}
    for mv in moves:
        if mv.get("family_key") != "ryu:tatsu":
            continue
        idx = mv.get("family_chain_index")
        if not idx:
            continue
        info = chains.setdefault(int(idx), {"members": [], "min": None, "max": None, "label": mv.get("family_chain_label"), "strengths": set()})
        info["members"].append(mv)
        st = _clean_label(mv.get("family_strength") or mv.get("family_strength_guess") or "")
        if st:
            try:
                info["strengths"].add(st)
            except Exception:
                pass
        addr = _family_addr(mv)
        if not addr:
            continue
        info["min"] = addr if info["min"] is None else min(info["min"], addr)
        info["max"] = addr if info["max"] is None else max(info["max"], addr)

    if not chains:
        return

    for mv in moves:
        if mv.get("family_key") != "ryu:tatsu" or mv.get("family_role") != "entry":
            continue
        addr = _family_addr(mv)
        if not addr:
            continue

        entry_strength = _clean_label(mv.get("family_strength") or "")
        best_idx = None
        best_score = None
        for idx, info in chains.items():
            lo = info.get("min")
            hi = info.get("max")
            if lo is None or hi is None:
                continue
            if lo <= addr <= hi:
                gap = 0
            elif addr > hi:
                gap = addr - hi
            else:
                gap = lo - addr
            if gap > 0x500:
                continue
            strengths = info.get("strengths") or set()
            if entry_strength and strengths and entry_strength not in strengths:
                continue
            score = gap
            if best_score is None or score < best_score:
                best_idx = idx
                best_score = score

        if best_idx is not None:
            info = chains[best_idx]
            chain_label = info.get("label") or f"Chain {best_idx}"
            mv["family_chain_index"] = best_idx
            mv["family_chain_label"] = chain_label
            mv["family_group_key"] = f"ryu:tatsu:{_safe_key(chain_label)}"
            mv["family_group_label"] = f"Tatsu {chain_label}"
            phase = _text(mv.get("family_phase") or "Entry")
            strength = _clean_label(mv.get("family_strength"))
            suffix = f" / {strength}" if strength else ""
            mv["family_link_label"] = f"Tatsu / {chain_label}{suffix} / {phase}"
            mv["family_confidence"] = "near-chain+id-confirmed"
        else:
            mv["family_group_key"] = "ryu:tatsu:entry_helpers"
            mv["family_group_label"] = "Tatsu entry/helper rows"
            src = _text(mv.get("source"))
            if src == "strict":
                mv["family_confidence"] = "id-reused-confirmed"
                phase = _text(mv.get("family_phase") or "Entry")
                strength = _clean_label(mv.get("family_strength"))
                suffix = f" / {strength}" if strength else ""
                mv["family_link_label"] = f"Tatsu / Entry-helper{suffix} / {phase}"


def _phase_sort_value(phase: str) -> int:
    return _PHASE_ORDER.get(_clean_label(phase), 99)


def _annotate_generic_phase_chains(moves: List[Dict[str, Any]], char_name: Optional[str]) -> None:
    """Split generic phase sections into nearby chains for every character.

    This is the cross-character "reach" pass.  It uses the same display-only
    idea as Ryu Tatsu, but relies on name-derived family/phase labels and
    address order instead of Ryu-specific IDs.
    """
    by_family: Dict[str, List[Dict[str, Any]]] = {}
    for mv in moves:
        key = mv.get("family_key")
        if not key or str(key).startswith("ryu:tatsu"):
            continue
        if not mv.get("family_linkable"):
            continue
        by_family.setdefault(str(key), []).append(mv)

    for key, members in by_family.items():
        if len(members) < 2:
            continue

        sections = [m for m in members if m.get("family_role") == "section" and _family_addr(m)]
        if len(sections) < 2:
            continue

        sections.sort(key=_family_addr)
        chains: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        last_addr: Optional[int] = None
        last_order = -1

        def close_current() -> None:
            nonlocal current, last_addr, last_order
            if current:
                chains.append(current)
            current = []
            last_addr = None
            last_order = -1

        for mv in sections:
            phase = _text(mv.get("family_phase"))
            order = _phase_sort_value(phase)
            addr = _family_addr(mv)
            gap = (addr - last_addr) if last_addr is not None else 0
            # Start/startup-like phases naturally begin a new script chain.  A
            # decreasing phase order or a large memory gap also starts a new one.
            if current and (order <= last_order or order in {0, 1, 2} or gap > 0x1800):
                close_current()
            current.append(mv)
            last_addr = addr
            last_order = order
        close_current()

        if not chains:
            continue

        label = _clean_label(members[0].get("family_label")) or key
        for idx, chain in enumerate(chains, start=1):
            contexts = [m.get("family_context") for m in chain if m.get("family_context") and m.get("family_context") != "Section"]
            context = _clean_label(contexts[0]) if contexts else "Ground"
            strength = _infer_chain_strength(label, idx, chain or members)
            chain_bits = []
            if context:
                chain_bits.append(context)
            if strength:
                chain_bits.append(strength)
            chain_bits.append("chain")
            chain_label = " ".join(chain_bits)
            group_key = f"{key}:chain:{idx}"
            group_label = f"{label} {chain_label}"
            for mv in chain:
                phase = _text(mv.get("family_phase") or "Section")
                mv["family_chain_index"] = idx
                mv["family_chain_label"] = chain_label
                mv["family_group_key"] = group_key
                mv["family_group_label"] = group_label
                if strength and not mv.get("family_strength"):
                    mv["family_strength_guess"] = strength
                    mv["family_strength"] = strength
                mv["family_link_label"] = " / ".join([b for b in (label, chain_label, phase) if b])
                mv["family_confidence"] = "generic-chain+strength-inferred" if strength else "generic-chain"

        # Attach nearby entries to the nearest same-family chain.  This gives
        # non-Ryu characters a usable wrapper/section view without making the
        # scanner pretend it knows the input route.
        chain_ranges: List[Tuple[int, int, int, str, str]] = []
        for idx, chain in enumerate(chains, start=1):
            addrs = [_family_addr(m) for m in chain if _family_addr(m)]
            if not addrs:
                continue
            chain_label = chain[0].get("family_chain_label") or f"chain {idx}"
            group_key = chain[0].get("family_group_key") or f"{key}:chain:{idx}"
            group_label = chain[0].get("family_group_label") or f"{label} {chain_label}"
            chain_ranges.append((idx, min(addrs), max(addrs), str(group_key), str(group_label)))

        for mv in members:
            if mv.get("family_role") != "entry" or mv.get("family_chain_index"):
                continue
            addr = _family_addr(mv)
            if not addr:
                continue
            best = None
            best_gap = None
            entry_strength = _clean_label(mv.get("family_strength") or "")
            for idx, lo, hi, gkey, glabel in chain_ranges:
                if lo <= addr <= hi:
                    gap = 0
                elif addr > hi:
                    gap = addr - hi
                else:
                    gap = lo - addr
                if gap > 0x600:
                    continue
                chain_members = [m for m in members if m.get("family_chain_index") == idx]
                chain_strength = _infer_chain_strength(label, idx, chain_members or members)
                if entry_strength and chain_strength and entry_strength != chain_strength:
                    continue
                if best_gap is None or gap < best_gap:
                    best = (idx, gkey, glabel, chain_strength)
                    best_gap = gap
            if best:
                idx, gkey, glabel, chain_strength = best
                mv["family_chain_index"] = idx
                mv["family_group_key"] = gkey
                mv["family_group_label"] = glabel
                context = _clean_label(mv.get("family_context")) or "Ground"
                strength = entry_strength or chain_strength
                phase = _clean_label(mv.get("family_phase") or "Entry")
                if strength and not mv.get("family_strength"):
                    mv["family_strength_guess"] = strength
                    mv["family_strength"] = strength
                chain_label = _clean_label(glabel.replace(label, "", 1).strip()) or "chain"
                mv["family_link_label"] = " / ".join([b for b in (label, chain_label, phase) if b])
                mv["family_confidence"] = "generic-near-chain+strength-inferred" if chain_strength else "generic-near-chain"



def _attach_unlabeled_rows_to_nearby_families(moves: List[Dict[str, Any]]) -> None:
    """Attach obvious unnamed/filler scout rows to nearby named families.

    This keeps raw helper/phase records near the human move they probably belong
    to without changing write targeting.  It only runs when the family already
    has at least one named lookup row, so random filler does not create new fake
    families.
    """
    groups: Dict[str, Dict[str, Any]] = {}
    for mv in moves:
        if not mv.get("family_linkable"):
            continue
        gkey = str(mv.get("family_group_key") or mv.get("family_key") or "")
        if not gkey:
            continue
        addr = _family_addr(mv)
        if not addr:
            continue
        info = groups.setdefault(gkey, {
            "members": [],
            "min": None,
            "max": None,
            "label": mv.get("family_label") or mv.get("family_group_label") or gkey,
            "group_label": mv.get("family_group_label") or mv.get("family_label") or gkey,
            "named": False,
        })
        info["members"].append(mv)
        info["named"] = bool(info.get("named") or _is_named_lookup_row(mv))
        info["min"] = addr if info["min"] is None else min(info["min"], addr)
        info["max"] = addr if info["max"] is None else max(info["max"], addr)

    candidates = []
    for gkey, info in groups.items():
        members = info.get("members") or []
        if not info.get("named") or len(members) < 2:
            continue
        # Entry-only families such as plain projectile L/M/H can span a wide
        # address area; absorbing every anim_ row near them makes noise. Only
        # absorb unnamed rows into families that already have section/chain
        # evidence.
        if not any(m.get("family_role") == "section" or m.get("family_chain_index") for m in members):
            continue
        if info.get("min") is None or info.get("max") is None:
            continue
        candidates.append((gkey, int(info["min"]), int(info["max"]), info))

    if not candidates:
        return

    for mv in moves:
        if not _can_absorb_unnamed_row(mv):
            continue
        addr = _family_addr(mv)
        if not addr:
            continue
        best = None
        best_gap = None
        for gkey, lo, hi, info in candidates:
            member_addrs = [_family_addr(m) for m in (info.get("members") or []) if _family_addr(m)]
            if not member_addrs:
                continue
            gap = min(abs(addr - ma) for ma in member_addrs)
            # Range alone is not enough; big super/family groups can cover a
            # whole script neighborhood.  Only absorb rows that are physically
            # close to an existing linked member.
            if gap > 0x140:
                continue
            if best_gap is None or gap < best_gap:
                best = (gkey, info)
                best_gap = gap
        if not best:
            continue
        gkey, info = best
        members = info.get("members") or []
        label = _clean_label(info.get("label") or info.get("group_label") or gkey)
        group_label = _clean_label(info.get("group_label") or label)
        context = "Ground"
        chain_index = None
        chain_label = ""
        strength = ""
        # Use the nearest named/grouped member as context for the orphan row.
        nearest = None
        nearest_gap = None
        for m in members:
            ma = _family_addr(m)
            if not ma:
                continue
            gap = abs(addr - ma)
            if nearest_gap is None or gap < nearest_gap:
                nearest = m
                nearest_gap = gap
        if nearest:
            context = _clean_label(nearest.get("family_context") or context) or context
            if context == "Section":
                context = ""
            chain_index = nearest.get("family_chain_index")
            chain_label = _clean_label(nearest.get("family_chain_label") or "")
            strength = _clean_label(nearest.get("family_strength") or nearest.get("family_strength_guess") or "")
        if not strength and chain_index:
            strength = _infer_chain_strength(label, int(chain_index), members)
        mv["family_key"] = str(nearest.get("family_key") if nearest else gkey)
        mv["family_label"] = label
        mv["family_role"] = "section"
        mv["family_phase"] = _clean_label(mv.get("family_phase") or "Linked section")
        mv["family_context"] = context
        mv["family_strength"] = strength
        if strength:
            mv["family_strength_guess"] = strength
        mv["family_linkable"] = True
        mv["family_group_key"] = gkey
        mv["family_group_label"] = group_label
        if chain_index:
            mv["family_chain_index"] = chain_index
        if chain_label:
            mv["family_chain_label"] = chain_label
        bits = [label]
        if chain_label:
            bits.append(chain_label)
        else:
            if context:
                bits.append(context)
            if strength:
                bits.append(strength)
        bits.append(mv["family_phase"])
        mv["family_link_label"] = " / ".join([b for b in bits if b])
        mv["family_confidence"] = "nearby-unnamed-linked"


def _prune_singleton_generic_families(moves: List[Dict[str, Any]]) -> None:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for mv in moves:
        if not mv.get("family_linkable"):
            continue
        key = str(mv.get("family_group_key") or mv.get("family_key") or "")
        if key:
            groups.setdefault(key, []).append(mv)

    for key, members in groups.items():
        if len(members) >= 2:
            continue
        mv = members[0]
        conf = _text(mv.get("family_confidence"))
        # Keep exact/id-confirmed singletons if they were intentionally tagged,
        # but do not create one-row headers for generic name guesses.
        if conf.startswith("name-generic"):
            mv.update({
                "family_key": "",
                "family_label": "",
                "family_role": "",
                "family_phase": "",
                "family_context": "",
                "family_strength": "",
                "family_link_label": "",
                "family_group_key": "",
                "family_group_label": "",
                "family_confidence": "",
                "family_linkable": False,
            })


def annotate_move_families(moves: Iterable[Dict[str, Any]], char_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Attach family fields in-place and return the same list as a list."""
    out = list(moves or [])
    for mv in out:
        meta = classify_move(mv, char_name)
        mv.update(meta)

    if _is_ryu(char_name):
        _annotate_ryu_tatsu_section_chains(out)
        _attach_nearby_ryu_tatsu_entries_to_chains(out)

    _annotate_generic_phase_chains(out, char_name)
    _attach_unlabeled_rows_to_nearby_families(out)
    _attach_multihit_helper_rows_to_nearby_named_rows(out, char_name)
    _prune_singleton_generic_families(out)

    return out
