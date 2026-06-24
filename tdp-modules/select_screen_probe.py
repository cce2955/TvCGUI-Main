from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
import re
import tkinter as tk
from tkinter import messagebox

try:
    from tk_host import tk_call
except Exception:  # pragma: no cover
    tk_call = None


@dataclass(frozen=True)
class SelectProbeTarget:
    label: str
    addr: int
    size: int
    group: str
    note: str = ""


def _z(label: str, addr: int, text: str, group: str, note: str = "") -> SelectProbeTarget:
    # Include the NUL terminator where these are normal C strings.
    return SelectProbeTarget(label, int(addr), len(text.encode("ascii")) + 1, group, note)


# Character-select probe buckets from the 2026-06-01 select-screen MEM dump.
# These are RAM-only test writes.  The popup lets the operator zero/restore single
# candidates or a full bucket while watching the select screen.
SELECT_PROBE_TARGETS: tuple[SelectProbeTarget, ...] = (
    # The first confirmed bucket: this affects the large back panels/signage.
    _z("back select_xxx", 0x90818438, "select_xxx", "Back panels"),
    _z("back select_ prefix", 0x90818444, "select_", "Back panels"),
    _z("back name_xxx", 0x9081844C, "name_xxx", "Back panels"),
    _z("back name_ prefix", 0x90818458, "name_", "Back panels"),
    _z("preview mof_gac", 0x90818460, "mof_gac", "Preview / silhouette state", "Model/preview resource binding. Related to silhouette/standing preview behavior."),
    _z("preview mof_ prefix", 0x90818468, "mof_", "Preview / silhouette state", "Observed: zeroing this keeps the silhouettes cycling between the last two characters. This is preview/silhouette state, not simple back-panel text."),
    _z("back gac tag", 0x90818470, "gac", "Back panels"),
    _z("back select_yuk", 0x90818474, "select_yuk", "Back panels"),
    _z("back name_vjo", 0x90818480, "name_vjo", "Back panels"),
    _z("back icon_random0", 0x9081848C, "icon_random0", "Back panels"),
    _z("back icon_random1", 0x9081849C, "icon_random1", "Back panels"),
    _z("back 2ndmoji", 0x908184B0, "2ndmoji", "Back panels"),

    # Loaded scene/layout names.  This is useful for figuring out which menu
    # scene variant is currently active, but can be more disruptive.
    _z("scene stageselect00 A", 0x9081CB24, "stageselect00", "Scene layout names"),
    _z("scene characterselect00 A", 0x9081CB34, "characterselect00", "Scene layout names"),
    _z("scene stageselect01 A", 0x9081CB58, "stageselect01", "Scene layout names"),
    _z("scene characterselect01 A", 0x9081CB68, "characterselect01", "Scene layout names"),
    _z("scene stageselect00 B", 0x9081CB8C, "stageselect00", "Scene layout names"),
    _z("scene characterselect00b", 0x9081CB9C, "characterselect00b", "Scene layout names"),
    _z("scene stageselect01 B", 0x9081CBC0, "stageselect01", "Scene layout names"),
    _z("scene characterselect01b", 0x9081CBD0, "characterselect01b", "Scene layout names"),
    _z("scene stageselect00 C", 0x9081CBF4, "stageselect00", "Scene layout names"),
    _z("scene characterselect00c", 0x9081CC04, "characterselect00c", "Scene layout names"),
    _z("scene stageselect01 C", 0x9081CC28, "stageselect01", "Scene layout names"),
    _z("scene characterselect01c", 0x9081CC38, "characterselect01c", "Scene layout names"),

    # Layout copies that referenced specific select/name panes.
    _z("layout select_gac A", 0x9081B440, "select_gac", "Panel layout copies"),
    _z("layout select_chu A", 0x9081B44C, "select_chu", "Panel layout copies"),
    _z("layout select_ryu A", 0x9081B468, "select_ryu", "Panel layout copies"),
    _z("layout select_chu B", 0x9081B474, "select_chu", "Panel layout copies"),
    _z("layout select_gac B", 0x9081B490, "select_gac", "Panel layout copies"),
    _z("layout select_chu C", 0x9081B49C, "select_chu", "Panel layout copies"),
    _z("layout select_ryu B", 0x9081B4B8, "select_ryu", "Panel layout copies"),
    _z("layout select_chu D", 0x9081B4C4, "select_chu", "Panel layout copies"),
    _z("layout name_gac A", 0x9081B4E0, "name_gac", "Panel layout copies"),
    _z("layout name_vjo A", 0x9081B4EC, "name_vjo", "Panel layout copies"),
    _z("layout name_ryu A", 0x9081B508, "name_ryu", "Panel layout copies"),
    _z("layout name_vjo B", 0x9081B514, "name_vjo", "Panel layout copies"),
    _z("layout name_gac B", 0x9081B530, "name_gac", "Panel layout copies"),
    _z("layout name_vjo C", 0x9081B53C, "name_vjo", "Panel layout copies"),
    _z("layout name_ryu B", 0x9081B558, "name_ryu", "Panel layout copies"),
    _z("layout name_vjo D", 0x9081B564, "name_vjo", "Panel layout copies"),

    # 1st/2nd character panel text and node names.
    _z("text moji1 A", 0x90821E34, "moji1", "1st/2nd text panels"),
    _z("text chara2p_2 A", 0x90821E48, "chara2p_2", "1st/2nd text panels"),
    _z("text select_ prefix A", 0x90821ED8, "select_", "1st/2nd text panels"),
    _z("text select_gac A", 0x90821EE0, "select_gac", "1st/2nd text panels"),
    _z("text name_ prefix A", 0x90821F0C, "name_", "1st/2nd text panels"),
    _z("text name_vjo A", 0x90821F14, "name_vjo", "1st/2nd text panels"),
    _z("text select_gac B", 0x90821F58, "select_gac", "1st/2nd text panels"),
    _z("text select_gac C", 0x90821F64, "select_gac", "1st/2nd text panels"),
    _z("text name_gac A", 0x90821F98, "name_gac", "1st/2nd text panels"),
    _z("text name_vjo B", 0x90821FA4, "name_vjo", "1st/2nd text panels"),
    _z("text chara2p_2 B", 0x90821FC8, "chara2p_2", "1st/2nd text panels"),
    _z("text moji1 B", 0x90821FE0, "moji1", "1st/2nd text panels"),
    _z("text moji1 C", 0x9082202C, "moji1", "1st/2nd text panels"),
    _z("text chara2p_2 C", 0x90822040, "chara2p_2", "1st/2nd text panels"),
    _z("text select_ prefix B", 0x908220D0, "select_", "1st/2nd text panels"),
    _z("text select_chu A", 0x908220D8, "select_chu", "1st/2nd text panels"),
    _z("text name_ prefix B", 0x90822104, "name_", "1st/2nd text panels"),
    _z("text name_vjo C", 0x9082210C, "name_vjo", "1st/2nd text panels"),
    _z("text select_gac D", 0x90822150, "select_gac", "1st/2nd text panels"),
    _z("text select_chu B", 0x9082215C, "select_chu", "1st/2nd text panels"),
    _z("text name_gac B", 0x90822190, "name_gac", "1st/2nd text panels"),
    _z("text name_vjo D", 0x9082219C, "name_vjo", "1st/2nd text panels"),
    _z("text chara2p_2 D", 0x908221C0, "chara2p_2", "1st/2nd text panels"),
    _z("text moji1 D", 0x908221D8, "moji1", "1st/2nd text panels"),
    _z("text moji1 E", 0x90822234, "moji1", "1st/2nd text panels"),
    _z("text chara2p_2 E", 0x90822248, "chara2p_2", "1st/2nd text panels"),
    _z("text select_ prefix C", 0x908222D8, "select_", "1st/2nd text panels"),
    _z("text select_gac E", 0x908222E0, "select_gac", "1st/2nd text panels"),
    _z("text name_ prefix C", 0x9082230C, "name_", "1st/2nd text panels"),
    _z("text name_vjo E", 0x90822314, "name_vjo", "1st/2nd text panels"),
    _z("text 2ndmoji A", 0x908223D0, "2ndmoji", "1st/2nd text panels"),
    _z("text 1stmoji A", 0x908223D8, "1stmoji", "1st/2nd text panels"),
    _z("text chara2p_2 F", 0x908223E8, "chara2p_2", "1st/2nd text panels"),
    _z("text moji1 F", 0x90822400, "moji1", "1st/2nd text panels"),
    _z("text moji1 G", 0x9082244C, "moji1", "1st/2nd text panels"),
    _z("text chara2p_2 G", 0x90822460, "chara2p_2", "1st/2nd text panels"),
    _z("text select_ prefix D", 0x908224F0, "select_", "1st/2nd text panels"),
    _z("text select_chu C", 0x908224F8, "select_chu", "1st/2nd text panels"),
    _z("text name_ prefix D", 0x90822524, "name_", "1st/2nd text panels"),
    _z("text name_vjo G", 0x9082252C, "name_vjo", "1st/2nd text panels"),
    _z("text select_gac H", 0x90822570, "select_gac", "1st/2nd text panels"),
    _z("text select_chu D", 0x9082257C, "select_chu", "1st/2nd text panels"),
    _z("text name_gac D", 0x908225B0, "name_gac", "1st/2nd text panels"),
    _z("text name_vjo H", 0x908225BC, "name_vjo", "1st/2nd text panels"),
    _z("text 2ndmoji B", 0x908225E8, "2ndmoji", "1st/2nd text panels"),
    _z("text 1stmoji B", 0x908225F0, "1stmoji", "1st/2nd text panels"),
    _z("text chara2p_2 H", 0x90822600, "chara2p_2", "1st/2nd text panels"),
    _z("text moji1 H", 0x90822618, "moji1", "1st/2nd text panels"),

    # Random has several independent copies.
    _z("random select_random0 A", 0x9083AC1C, "select_random0", "Random slot"),
    _z("random select_random0 B", 0x9083B100, "select_random0", "Random slot"),
    _z("random select_random0 C", 0x9083B2D8, "select_random0", "Random slot"),
    _z("random select_random0 D", 0x9083CBE0, "select_random0", "Random slot"),
    _z("random select_random1 A", 0x9083CCAC, "select_random1", "Random slot"),
    _z("random select_random2 A", 0x9083CD44, "select_random2", "Random slot"),
    _z("random select_random3 A", 0x9083CDA8, "select_random3", "Random slot"),
    _z("random select_random1 B", 0x9083CE48, "select_random1", "Random slot"),
    _z("random select_random2 B", 0x9083CF14, "select_random2", "Random slot"),
    _z("random select_random3 B", 0x9083CFAC, "select_random3", "Random slot"),
    _z("random select_random0 E", 0x9083D010, "select_random0", "Random slot"),
    _z("random select_random1 C", 0x9083D0B0, "select_random1", "Random slot"),
    _z("random select_random2 C", 0x9083D17C, "select_random2", "Random slot"),
    _z("random select_random3 C", 0x9083D214, "select_random3", "Random slot"),
    _z("random select_random0 F", 0x9083D278, "select_random0", "Random slot"),
    _z("random select_random1 D", 0x9083D318, "select_random1", "Random slot"),
    _z("random select_random2 D", 0x9083D3E4, "select_random2", "Random slot"),
    _z("random select_random3 D", 0x9083D47C, "select_random3", "Random slot"),
    _z("random select_random0 G", 0x9083D4E0, "select_random0", "Random slot"),

    # Player labels/nameplate nodes.
    _z("nameplate Name_1P", 0x9084E730, "Name_1P", "Player nameplates"),
    _z("nameplate Name_1P copy", 0x9084E760, "Name_1P", "Player nameplates"),
    _z("nameplate Face_1P", 0x9084E790, "Face_1P", "Player nameplates"),
    _z("nameplate Name_2P", 0x9084E7B0, "Name_2P", "Player nameplates"),
    _z("nameplate Name_2P copy", 0x9084E7E0, "Name_2P", "Player nameplates"),
    _z("nameplate Face_2P", 0x9084E810, "Face_2P", "Player nameplates"),
    _z("nameplate Name_3P", 0x9084E858, "Name_3P", "Player nameplates"),
    _z("nameplate Name_3P copy", 0x9084E888, "Name_3P", "Player nameplates"),
    _z("nameplate Face_3P", 0x9084E8B8, "Face_3P", "Player nameplates"),
    _z("nameplate Name_4P", 0x9084E8DC, "Name_4P", "Player nameplates"),
    _z("nameplate Name_4P copy", 0x9084E90C, "Name_4P", "Player nameplates"),
    _z("nameplate Face_4P", 0x9084E93C, "Face_4P", "Player nameplates"),
    _z("nameplate Name_1P_Big A", 0x9084E9E8, "Name_1P_Big", "Player nameplates"),
    _z("nameplate Name_1P_Big B", 0x9084EA1C, "Name_1P_Big", "Player nameplates"),
    _z("nameplate Name_2P_Big A", 0x9084EC28, "Name_2P_Big", "Player nameplates"),
    _z("nameplate Name_2P_Big B", 0x9084EC5C, "Name_2P_Big", "Player nameplates"),

    # String pool: useful for global resource-name effects, not likely the live wheel map by itself.
    _z("pool select_alx", 0x930DEDD0, "select_alx", "Resource string pool"),
    _z("pool select_bat", 0x930DEDE0, "select_bat", "Resource string pool"),
    _z("pool select_cas", 0x930DEDF0, "select_cas", "Resource string pool"),
    _z("pool select_chu", 0x930DEE00, "select_chu", "Resource string pool"),
    _z("pool select_dro", 0x930DEE10, "select_dro", "Resource string pool"),
    _z("pool select_fra", 0x930DEE20, "select_fra", "Resource string pool"),
    _z("pool select_gac", 0x930DEE30, "select_gac", "Resource string pool"),
    _z("pool select_gld", 0x930DEE40, "select_gld", "Resource string pool"),
    _z("pool select_ipa", 0x930DEE50, "select_ipa", "Resource string pool"),
    _z("pool select_joe", 0x930DEE60, "select_joe", "Resource string pool"),
    _z("pool select_jun", 0x930DEE70, "select_jun", "Resource string pool"),
    _z("pool select_krs", 0x930DEE80, "select_krs", "Resource string pool"),
    _z("pool select_mor", 0x930DEE90, "select_mor", "Resource string pool"),
    _z("pool select_pol", 0x930DEEA0, "select_pol", "Resource string pool"),
    _z("pool select_ptx", 0x930DEEB0, "select_ptx", "Resource string pool"),
    _z("pool select_random0", 0x930DEEC0, "select_random0", "Resource string pool"),
    _z("pool select_random1", 0x930DEED4, "select_random1", "Resource string pool"),
    _z("pool select_random2", 0x930DEEE8, "select_random2", "Resource string pool"),
    _z("pool select_random3", 0x930DEEFC, "select_random3", "Resource string pool"),
    _z("pool select_roc", 0x930DEF10, "select_roc", "Resource string pool"),
    _z("pool select_rol", 0x930DEF20, "select_rol", "Resource string pool"),
    _z("pool select_ryu", 0x930DEF30, "select_ryu", "Resource string pool"),
    _z("pool select_sak", 0x930DEF40, "select_sak", "Resource string pool"),
    _z("pool select_sil", 0x930DEF50, "select_sil", "Resource string pool"),
    _z("pool select_tek", 0x930DEF60, "select_tek", "Resource string pool"),
    _z("pool select_tkb", 0x930DEF70, "select_tkb", "Resource string pool"),
    _z("pool select_ts2", 0x930DEF80, "select_ts2", "Resource string pool"),
    _z("pool select_vjo", 0x930DEF90, "select_vjo", "Resource string pool"),
    _z("pool select_ya2", 0x930DEFA0, "select_ya2", "Resource string pool"),
    _z("pool select_yat", 0x930DEFB0, "select_yat", "Resource string pool"),
    _z("pool select_yuk", 0x930DEFC0, "select_yuk", "Resource string pool"),
    _z("pool select_zer", 0x930DEFD0, "select_zer", "Resource string pool"),

    # Risky candidates. These look more like game-side tag/record tables.
    # Zeroing them can break selection flow, so restore quickly after each test.
    SelectProbeTarget("risky tag list CMN..FRA chunk", 0x80563CE0, 124, "Risky roster records", "broad MEM1 character/stage tag table"),
    SelectProbeTarget("risky roster record copy A GLD", 0x80365A88, 4, "Risky roster records"),
    SelectProbeTarget("risky roster record copy A YAT", 0x80365AA8, 4, "Risky roster records"),
    SelectProbeTarget("risky roster record copy A ALX", 0x80365AC8, 4, "Risky roster records"),
    SelectProbeTarget("risky roster record copy A DRO", 0x80365AE8, 4, "Risky roster records"),
    SelectProbeTarget("risky roster record copy A KRS", 0x80365B28, 4, "Risky roster records"),
    SelectProbeTarget("risky roster record copy A JUN", 0x80365B68, 4, "Risky roster records"),
    SelectProbeTarget("risky roster record copy A ROL", 0x80365B88, 4, "Risky roster records"),
    SelectProbeTarget("risky roster record copy A PTX", 0x80365BA8, 4, "Risky roster records"),
    SelectProbeTarget("risky roster record copy A KEN", 0x80365BC8, 4, "Risky roster records"),
    SelectProbeTarget("risky roster record copy A RYU", 0x80365BE8, 4, "Risky roster records"),
    SelectProbeTarget("risky roster record copy A CHU", 0x80365C08, 4, "Risky roster records"),
    SelectProbeTarget("risky roster record copy A TEK", 0x80365C28, 4, "Risky roster records"),
    SelectProbeTarget("risky roster record copy B GLD", 0x803C1D90, 4, "Risky roster records"),
    SelectProbeTarget("risky roster record copy B YAT", 0x803C1DB0, 4, "Risky roster records"),
    SelectProbeTarget("risky roster record copy B RYU", 0x803C1EF0, 4, "Risky roster records"),
    SelectProbeTarget("risky roster record copy C GLD", 0x803EF2F8, 4, "Risky roster records"),
    SelectProbeTarget("risky roster record copy C YAT", 0x803EF318, 4, "Risky roster records"),
    SelectProbeTarget("risky roster record copy C RYU", 0x803EF458, 4, "Risky roster records"),
)


# Dump-derived master character ID -> tag table. This is the first actually
# selector-adjacent target from the dump: it matches the runtime character IDs
# the rest of Continuo sees, unlike the older 0x1C MEM1 record runs that only
# cover a partial/legacy roster. Test these one at a time.
CHAR_ID_TAGS: tuple[tuple[int, str, str], ...] = (
    (0, "CMN", "common / invalid fallback"),
    (1, "GAC", "Ken the Eagle"),
    (2, "CAS", "Casshan"),
    (3, "TEK", "Tekkaman"),
    (4, "POL", "Polimar"),
    (5, "YAT", "Yatterman-1"),
    (6, "DRO", "Doronjo"),
    (7, "IPA", "Ippatsuman"),
    (8, "JUN", "Jun"),
    (9, "TS2", "Tekkaman / legacy TS2 tag"),
    (10, "KRS", "Karas"),
    (11, "GLD", "Gold Lightan"),
    (12, "RYU", "Ryu"),
    (13, "CHU", "Chun-Li"),
    (14, "BAT", "Batsu"),
    (15, "MOR", "Morrigan"),
    (16, "ALX", "Alex"),
    (17, "VJO", "Viewtiful Joe"),
    (18, "ROC", "Roll / Rockman-family tag"),
    (19, "ROL", "Roll"),
    (20, "SAK", "Saki"),
    (21, "YUK", "Yatterman-2 / Yuki tag"),
    (22, "PTX", "PTX-40A"),
    (23, "TK1", "Tekkaman Blade alt/internal 1"),
    (24, "TK2", "Tekkaman Blade alt/internal 2"),
    (25, "TK3", "Tekkaman Blade alt/internal 3"),
    (26, "TKB", "Tekkaman Blade"),
    (27, "JOE", "Joe the Condor"),
    (28, "YA2", "Yatterman-2"),
    (29, "ZER", "Zero"),
    (30, "FRA", "Frank West"),
)
CHAR_ID_BY_TAG: dict[str, int] = {tag: char_id for char_id, tag, _name in CHAR_ID_TAGS}
CHAR_ID_TABLE_BASE = 0x80563CE0

SELECTOR_ID_MAP_TARGETS: tuple[SelectProbeTarget, ...] = tuple(
    SelectProbeTarget(
        f"selector id {char_id:02d} {tag} {name}",
        CHAR_ID_TABLE_BASE + char_id * 4,
        4,
        "Selector ID map",
        "Master char-id -> 3-letter tag table from the dump. If zeroing this affects selected/locked character, this is selector-side, not just visual.",
    )
    for char_id, tag, name in CHAR_ID_TAGS
)

GROUP_ORDER: tuple[str, ...] = (
    "Back panels",
    "Preview / silhouette state",
    "Scene layout names",
    "Panel layout copies",
    "1st/2nd text panels",
    "Random slot",
    "Player nameplates",
    "Resource string pool",
    "Trace results",
    "Selector ID map",
    "Risky roster records",
)

_OPEN_WINDOW: tk.Toplevel | None = None

_BG = "#0d1018"
_PANEL = "#171b27"
_BORDER = "#31384d"
_TEXT = "#e9edf7"
_MUTED = "#aeb8cc"
_ACCENT = "#89a7ff"
_ACTIVE = "#2a3553"
_DANGER = "#4a2230"
_OK = "#58e09a"
_WARN = "#ffcf6a"


def _key_for(target: SelectProbeTarget) -> str:
    return f"0x{target.addr:08X}:{target.size}"


def _state_trace_targets(state: dict[str, Any] | None) -> list[SelectProbeTarget]:
    if not isinstance(state, dict):
        return []
    items = state.get("trace_targets") or []
    out: list[SelectProbeTarget] = []
    for item in items:
        if isinstance(item, SelectProbeTarget):
            out.append(item)
        elif isinstance(item, dict):
            try:
                out.append(
                    SelectProbeTarget(
                        str(item.get("label") or "trace target"),
                        int(item.get("addr") or 0),
                        max(1, int(item.get("size") or 1)),
                        str(item.get("group") or "Trace results"),
                        str(item.get("note") or ""),
                    )
                )
            except Exception:
                pass
    return out


def _targets(state: dict[str, Any] | None = None) -> list[SelectProbeTarget]:
    return list(SELECT_PROBE_TARGETS) + list(SELECTOR_ID_MAP_TARGETS) + _state_trace_targets(state)


def _target_count(state: dict[str, Any] | None = None) -> int:
    return len(_targets(state))


def _target_at(state: dict[str, Any], index: int) -> SelectProbeTarget:
    items = _targets(state)
    idx = int(index)
    if idx < 0 or idx >= len(items):
        raise IndexError(f"invalid target index {idx}")
    return items[idx]


def _write_bytes(write_fn: Callable[[int, bytes], Any], addr: int, data: bytes) -> bool:
    result = write_fn(int(addr), bytes(data))
    return result is None or bool(result)


def _ensure_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        state = new_probe_state()
    state.setdefault("index", 0)
    state.setdefault("saved", {})
    state.setdefault("last", "")
    state.setdefault("last_error", "")
    state.setdefault("modified_count", len(state.get("saved") or {}))
    state.setdefault("trace_targets", [])
    state["total"] = _target_count(state)
    return state


def _save_original_if_needed(state: dict[str, Any], target: SelectProbeTarget, read_fn: Callable[[int, int], bytes]) -> None:
    saved = state.setdefault("saved", {})
    key = _key_for(target)
    if key not in saved:
        original = read_fn(target.addr, target.size)
        saved[key] = bytes(original or b"")[: target.size]


def new_probe_state() -> dict[str, Any]:
    return {
        "index": 0,
        "saved": {},
        "last": "",
        "last_error": "",
        "modified_count": 0,
        "total": len(SELECT_PROBE_TARGETS),
        "trace_targets": [],
        "trace_last": "",
    }


def zero_probe_target(
    state: dict[str, Any],
    target_index: int,
    read_fn: Callable[[int, int], bytes],
    write_fn: Callable[[int, bytes], Any],
) -> dict[str, Any]:
    state = _ensure_state(state)
    idx = int(target_index)
    if idx < 0 or idx >= _target_count(state):
        return {"ok": False, "message": f"invalid target index {idx}"}
    target = _target_at(state, idx)
    try:
        _save_original_if_needed(state, target, read_fn)
        _write_bytes(write_fn, target.addr, b"\x00" * target.size)
        state["modified_count"] = len(state.get("saved") or {})
        state["last_error"] = ""
        state["last"] = f"zeroed {target.group}: {target.label} @ 0x{target.addr:08X} len {target.size}"
        print(f"[select probe] {state['last']}", flush=True)
        return {"ok": True, "target": target, "message": state["last"]}
    except Exception as e:
        state["last_error"] = repr(e)
        state["last"] = f"failed {target.group}: {target.label} @ 0x{target.addr:08X}: {e!r}"
        print(f"[select probe] {state['last']}", flush=True)
        return {"ok": False, "target": target, "message": state["last"], "error": repr(e)}


def restore_probe_target(
    state: dict[str, Any],
    target_index: int,
    write_fn: Callable[[int, bytes], Any],
) -> dict[str, Any]:
    state = _ensure_state(state)
    idx = int(target_index)
    if idx < 0 or idx >= _target_count(state):
        return {"ok": False, "message": f"invalid target index {idx}"}
    target = _target_at(state, idx)
    key = _key_for(target)
    saved = state.setdefault("saved", {})
    if key not in saved:
        state["last"] = f"nothing saved for {target.group}: {target.label}"
        return {"ok": True, "message": state["last"]}
    try:
        data = bytes(saved.pop(key) or b"")
        _write_bytes(write_fn, target.addr, data)
        state["modified_count"] = len(saved)
        state["last_error"] = ""
        state["last"] = f"restored {target.group}: {target.label} @ 0x{target.addr:08X}"
        print(f"[select probe] {state['last']}", flush=True)
        return {"ok": True, "target": target, "message": state["last"]}
    except Exception as e:
        state["last_error"] = repr(e)
        state["last"] = f"restore failed {target.group}: {target.label} @ 0x{target.addr:08X}: {e!r}"
        print(f"[select probe] {state['last']}", flush=True)
        return {"ok": False, "target": target, "message": state["last"], "error": repr(e)}


def zero_probe_group(
    state: dict[str, Any],
    group: str,
    read_fn: Callable[[int, int], bytes],
    write_fn: Callable[[int, bytes], Any],
) -> dict[str, Any]:
    state = _ensure_state(state)
    ok = 0
    failed = 0
    for i, t in enumerate(_targets(state)):
        if t.group != group:
            continue
        res = zero_probe_target(state, i, read_fn, write_fn)
        if res.get("ok"):
            ok += 1
        else:
            failed += 1
    state["last"] = f"zeroed group {group}: {ok} ok" + (f", {failed} failed" if failed else "")
    return {"ok": failed == 0, "zeroed": ok, "failed": failed, "message": state["last"]}


def restore_probe_group(
    state: dict[str, Any],
    group: str,
    write_fn: Callable[[int, bytes], Any],
) -> dict[str, Any]:
    state = _ensure_state(state)
    ok = 0
    failed = 0
    items = _targets(state)
    for i in range(len(items) - 1, -1, -1):
        t = items[i]
        if t.group != group:
            continue
        res = restore_probe_target(state, i, write_fn)
        if res.get("ok"):
            ok += 1
        else:
            failed += 1
    state["last"] = f"restored group {group}: {ok} checked" + (f", {failed} failed" if failed else "")
    return {"ok": failed == 0, "restored": ok, "failed": failed, "message": state["last"]}


def zero_next_probe(
    state: dict[str, Any],
    read_fn: Callable[[int, int], bytes],
    write_fn: Callable[[int, bytes], Any],
) -> dict[str, Any]:
    state = _ensure_state(state)
    total = _target_count(state)
    idx = int(state.get("index") or 0) % max(1, total)
    res = zero_probe_target(state, idx, read_fn, write_fn)
    if res.get("ok"):
        state["index"] = (idx + 1) % total
        state["last"] = f"{idx + 1}/{total} {state.get('last', '')}"
    return res


def restore_all_probes(
    state: dict[str, Any],
    write_fn: Callable[[int, bytes], Any],
) -> dict[str, Any]:
    state = _ensure_state(state)
    saved = dict(state.get("saved") or {})
    restored = 0
    failed = 0
    for key, data in reversed(list(saved.items())):
        try:
            addr_s, _size_s = str(key).split(":", 1)
            addr = int(addr_s, 16)
            if _write_bytes(write_fn, addr, bytes(data or b"")):
                restored += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    state["saved"] = {}
    state["modified_count"] = 0
    state["last_error"] = "" if failed == 0 else f"{failed} restore failures"
    state["last"] = f"restored {restored} select-probe targets" + (f"; failed {failed}" if failed else "")
    print(f"[select probe] {state['last']}", flush=True)
    return {"ok": failed == 0, "restored": restored, "failed": failed, "message": state["last"]}


def probe_button_label(state: dict[str, Any]) -> str:
    try:
        mod = int((state or {}).get("modified_count") or 0)
    except Exception:
        mod = 0
    return "CS Probe*" if mod else "CS Probe"


def get_probe_debug_state(state: dict[str, Any]) -> dict[str, Any]:
    state = _ensure_state(state)
    saved = state.get("saved") or {}
    by_group: dict[str, int] = {g: 0 for g in GROUP_ORDER}
    for i, t in enumerate(_targets(state)):
        if _key_for(t) in saved:
            by_group[t.group] = by_group.get(t.group, 0) + 1
    return {
        "modified_count": len(saved),
        "total": _target_count(state),
        "trace_count": len(_state_trace_targets(state)),
        "trace_last": str(state.get("trace_last") or ""),
        "last": str(state.get("last") or ""),
        "last_error": str(state.get("last_error") or ""),
        "by_group": by_group,
    }


def _button(parent: tk.Misc, text: str, command: Callable[[], Any], *, danger: bool = False) -> tk.Button:
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=_DANGER if danger else _ACTIVE,
        fg=_TEXT,
        activebackground="#5c2b3c" if danger else "#35466c",
        activeforeground=_TEXT,
        relief="raised",
        bd=1,
        padx=8,
        pady=4,
        highlightthickness=1,
        highlightbackground=_BORDER,
        cursor="hand2",
    )


KNOWN_SELECT_NOTES = (
    "Quick notes:\n"
    "- This window is poking RAM-only character-select resources. Nothing here is permanent.\n"
    "- Back-panel/sign rows are visual/resource bindings, not wheel order.\n"
    "- 063 / 0x908221C0 / chara2p_2 D makes the right-side panels fall back to Ryu.\n"
    "- Ryu fallback usually means a broken character resource key dropped to the default path.\n"
    "- Actual wheel order / cursor index -> character map is still not nailed down.\n"
    "- Good hits: wheel face changes, selected fighter changes, lock-in model changes, or random-slot behavior changes.\n"
    "- For a useful visual hit like pool select_pol, Trace Selected tries to follow nearby sibling/record clues."
)

OBSERVED_EFFECTS_BY_ADDR: dict[int, str] = {
    0x90818460: "Likely preview model/resource binding. Test with cursor movement; restore if the standing preview/silhouette gets stuck.",
    0x90818468: "Observed: zeroing mof_ prefix keeps the silhouettes cycling between the last two characters regardless of cursor. Preview/silhouette state, not wheel order.",
    0x908184B0: "Observed family: 1st/2nd moji/panel entries control what the big back panels are showing.",
    0x908221C0: "Observed: zeroing this makes the right-side/P2 character panel fall back to Ryu. Likely P2 preview/resource binding, not wheel order.",
    0x930DEEA0: "Observed: zeroing pool select_pol removes Polimar/POOL from the wheel visually, but selection still works. This is a wheel icon/resource layer, not the selector map.",
    0x80563CF0: "Dump lead: POL is char_id 04 in the master ID tag table. This is more selector-adjacent than pool select_pol.",
    0x908223D0: "Observed family: 1st/2nd moji/panel entries control what the back panels are showing.",
    0x908223D8: "Observed family: 1st/2nd moji/panel entries control what the back panels are showing.",
    0x908225E8: "Observed family: 1st/2nd moji/panel entries control what the back panels are showing.",
    0x908225F0: "Observed family: 1st/2nd moji/panel entries control what the back panels are showing.",
}

# Exact resource-pool addresses from tvc_memdump_20260601_210854.
# Random entries are not 0x10-spaced, so the earlier probe window was partially
# misaligned after select_random0. These are now corrected.


GROUP_HINTS: dict[str, str] = {
    "Back panels": "Confirmed visual layer. Expect large signs/back panels to disappear, change, or fall back. 1st/2nd moji entries choose what those panels display.",
    "Preview / silhouette state": "Preview/silhouette resources. mof_ prefix is confirmed to affect the last-two-character silhouette cycle; restore quickly after each test.",
    "Scene layout names": "Loaded menu scene/layout names. Can affect which select-screen scene variant resolves.",
    "Panel layout copies": "Likely copies of select/name pane bindings. Visual effects are expected.",
    "1st/2nd text panels": "Panel text/resource nodes. chara2p hits can fall back to Ryu.",
    "Random slot": "Random select has several special copies. Watch random icon/slot animation/state.",
    "Player nameplates": "Player/face/nameplate nodes. Watch 1P/2P labels and big preview labels.",
    "Resource string pool": "Global select/name resource strings. Can have broad fallback effects. Trace a confirmed visual hit to chase its owner records.",
    "Trace results": "Focused candidates generated from Trace Selected. Test the top selector-ID row first, then exact-string rows, then old 0x1C records last.",
    "Selector ID map": "Dump-derived 0x80563CE0 char-id -> tag table. This is the cleanest selector-adjacent table found so far. Test one row at a time.",
    "Risky roster records": "Legacy/partial 0x1C MEM1 records. They do not include the full UAS roster; keep them last.",
}


def _known_effect_for(target: SelectProbeTarget) -> str:
    effect = OBSERVED_EFFECTS_BY_ADDR.get(int(target.addr), "")
    if effect:
        return effect
    return GROUP_HINTS.get(target.group, "")


TRACE_SEARCH_RANGES: tuple[tuple[str, int, int], ...] = (
    ("MEM1 select text", 0x80350000, 0x00008000),
    ("MEM1 roster records A", 0x80364000, 0x00003000),
    ("MEM1 roster records B", 0x803C1800, 0x00005500),
    ("MEM1 roster records C", 0x803EF000, 0x00003000),
    ("MEM1 char tag table", 0x80563000, 0x00001000),
    ("MEM2 active CS layout", 0x90800000, 0x00068000),
    ("MEM2 CS misc layout", 0x909FCE00, 0x00001000),
    ("MEM2 resource string pool", 0x930DE000, 0x00002000),
)


def _looks_ascii_token(text: str) -> str:
    # Prefer real resource keys from the label/note. This catches select_pol,
    # icon_random0, Name_1P, chara2p_2, and 3-letter character tags.
    for pattern in (
        r"select_[A-Za-z0-9]+",
        r"icon_[A-Za-z0-9]+",
        r"name_[A-Za-z0-9]+",
        r"chara[0-9]p[_A-Za-z0-9]+",
        r"Name_[A-Za-z0-9_]+",
        r"Face_[A-Za-z0-9_]+",
    ):
        hits = re.findall(pattern, text)
        if hits:
            return hits[-1]
    # Fall back to a 3-letter uppercase tag for risky roster rows.
    hits = re.findall(r"\b[A-Z]{3}\b", text)
    if hits:
        return hits[-1]
    return ""


def _trace_token_for(target: SelectProbeTarget) -> tuple[str, str]:
    token = _looks_ascii_token(f"{target.label} {target.note}")
    short = ""
    if token.startswith("select_"):
        short = token.split("_", 1)[1][:3].upper()
    elif re.fullmatch(r"[A-Z]{3}", token):
        short = token
    return token, short


def _find_all(data: bytes, needle: bytes, limit: int = 32) -> list[int]:
    out: list[int] = []
    if not needle:
        return out
    start = 0
    while len(out) < limit:
        pos = data.find(needle, start)
        if pos < 0:
            break
        out.append(pos)
        start = pos + 1
    return out


def _add_unique_trace_target(out: list[SelectProbeTarget], seen: set[tuple[int, int]], label: str, addr: int, size: int, note: str) -> None:
    if size <= 0:
        return
    key = (int(addr), int(size))
    if key in seen:
        return
    seen.add(key)
    out.append(SelectProbeTarget(label, int(addr), int(size), "Trace results", note))


def build_trace_targets_for(
    state: dict[str, Any],
    selected: SelectProbeTarget,
    read_fn: Callable[[int, int], bytes],
) -> dict[str, Any]:
    'Generate focused trace probes from a known visual/resource hit.\n\n    This intentionally runs only when the operator presses Trace Selected. It reads a\n    small set of known character-select ranges, finds sibling resource copies,\n    pointer-like references, and uppercase character-tag records such as POL.\n    '
    state = _ensure_state(state)
    token, short_tag = _trace_token_for(selected)
    if not token and not short_tag:
        return {"ok": False, "message": f"No traceable resource token found for {selected.label}"}

    new_targets: list[SelectProbeTarget] = []
    seen: set[tuple[int, int]] = set()
    ptr_needles = [int(selected.addr).to_bytes(4, "big", signed=False)]

    # First row: the directly traced resource. This prevents the trace
    # list from feeling detached from the visual hit that started it.
    _add_unique_trace_target(
        new_targets,
        seen,
        f"trace selected visual hit {selected.label}",
        selected.addr,
        selected.size,
        "Original selected visual/resource hit. Useful as a baseline; this is usually not the true selector.",
    )

    # Strongest current dump lead: 0x80563CE0 is the master char_id -> tag
    # table. For select_pol, this adds POL char_id 04 immediately instead of
    # from being cluttered by old partial 0x1C roster rows.
    if short_tag and short_tag in CHAR_ID_BY_TAG:
        char_id = CHAR_ID_BY_TAG[short_tag]
        _add_unique_trace_target(
            new_targets,
            seen,
            f"trace selector id {char_id:02d} {short_tag} in master ID map",
            CHAR_ID_TABLE_BASE + char_id * 4,
            4,
            "BEST NEXT TEST: master char-id -> tag entry from 0x80563CE0. If this changes locked/selected character, this is selector-side.",
        )

    range_hits = 0
    tag_hits = 0
    ptr_hits = 0
    errors: list[str] = []

    for range_name, base, size in TRACE_SEARCH_RANGES:
        try:
            chunk = bytes(read_fn(base, size) or b"")
        except Exception as e:
            errors.append(f"{range_name}: {e!r}")
            continue
        if not chunk:
            continue

        if token:
            for off in _find_all(chunk, token.encode("ascii"), limit=48):
                addr = base + off
                range_hits += 1
                zero_size = len(token) + 1
                _add_unique_trace_target(
                    new_targets,
                    seen,
                    f"trace string {token} in {range_name}",
                    addr,
                    zero_size,
                    "ASCII/resource copy found by Trace Selected. If this changes only visuals, it is still resource-layer.",
                )
                ptr_needles.append(int(addr).to_bytes(4, "big", signed=False))

        if short_tag:
            # Character tags are usually 4-byte uppercase records like POL\0.
            for off in _find_all(chunk, short_tag.encode("ascii") + b"\x00", limit=48):
                addr = base + off
                tag_hits += 1
                _add_unique_trace_target(
                    new_targets,
                    seen,
                    f"trace tag {short_tag} in {range_name}",
                    addr,
                    4,
                    "Uppercase character tag. If zeroing changes selected/locked character, this is close to selection logic.",
                )
                # Many MEM1 roster records in the dump are 0x1C-byte records that
                # start on the tag. Add sibling fields without forcing a full-record wipe.
                if range_name.startswith("MEM1 roster records"):
                    for rel in (4, 8, 12, 16, 20, 24):
                        _add_unique_trace_target(
                            new_targets,
                            seen,
                            f"trace {short_tag} record +0x{rel:02X} in {range_name}",
                            addr + rel,
                            4,
                            "Sibling field next to the character tag. Watch for cursor/selected-character changes, not just icon changes.",
                        )
                    _add_unique_trace_target(
                        new_targets,
                        seen,
                        f"trace {short_tag} whole 0x1C record in {range_name}",
                        addr,
                        0x1C,
                        "Riskier whole-record probe. Restore immediately if select flow breaks.",
                    )

        # Pointer/reference search, mostly for owner records that store a RAM address.
        for ptr in ptr_needles[:64]:
            for off in _find_all(chunk, ptr, limit=16):
                addr = base + off
                ptr_hits += 1
                _add_unique_trace_target(
                    new_targets,
                    seen,
                    f"trace pointer/ref near {range_name}",
                    addr,
                    4,
                    "4-byte big-endian pointer/reference to selected resource or one of its copies.",
                )
                for rel in (-8, -4, 4, 8, 12):
                    if addr + rel >= base:
                        _add_unique_trace_target(
                            new_targets,
                            seen,
                            f"trace pointer sibling {rel:+d} in {range_name}",
                            addr + rel,
                            4,
                            "Sibling field beside a pointer/reference hit.",
                        )

    # Keep the window responsive and the list useful. The trace is a focused
    # shortlist, not a full memory search dump. Prioritize the selector-ID row,
    # then exact visual/string rows, then old partial 0x1C record rows.
    def _trace_rank(t: SelectProbeTarget) -> tuple[int, int]:
        label = t.label.lower()
        note = t.note.lower()
        if "selector id" in label:
            return (0, t.addr)
        if "selected visual" in label:
            return (1, t.addr)
        if "trace string" in label:
            return (2, t.addr)
        if "char tag table" in label or "master id" in note:
            return (3, t.addr)
        if "record +" in label:
            return (4, t.addr)
        if "whole" in label:
            return (9, t.addr)
        return (5, t.addr)

    new_targets.sort(key=_trace_rank)
    new_targets = new_targets[:64]
    state["trace_targets"] = new_targets
    state["trace_last"] = (
        f"Trace for {selected.label}: token={token or '-'} tag={short_tag or '-'}; "
        f"added {len(new_targets)} targets; string hits={range_hits}, tag hits={tag_hits}, ptr hits={ptr_hits}"
        + (f"; read errors={len(errors)}" if errors else "")
    )
    state["total"] = _target_count(state)
    state["last"] = state["trace_last"]
    print(f"[select probe] {state['trace_last']}", flush=True)
    return {"ok": True, "added": len(new_targets), "message": state["trace_last"], "errors": errors}


def clear_trace_targets(state: dict[str, Any]) -> dict[str, Any]:
    state = _ensure_state(state)
    # Do not auto-restore here. Clear only removes the generated list. Any zeroed
    # trace writes are still tracked in saved and can be restored normally.
    count = len(_state_trace_targets(state))
    state["trace_targets"] = []
    state["trace_last"] = f"cleared {count} trace-result rows"
    state["total"] = _target_count(state)
    state["last"] = state["trace_last"]
    return {"ok": True, "cleared": count, "message": state["trace_last"]}


def open_select_probe_window(
    state: dict[str, Any],
    read_fn: Callable[[int, int], bytes],
    write_fn: Callable[[int, bytes], Any],
) -> None:
    state = _ensure_state(state)

    def _show(master: tk.Tk) -> None:
        global _OPEN_WINDOW
        try:
            if _OPEN_WINDOW is not None and _OPEN_WINDOW.winfo_exists():
                _OPEN_WINDOW.lift()
                _OPEN_WINDOW.focus_force()
                return
        except Exception:
            _OPEN_WINDOW = None

        win = tk.Toplevel(master)
        _OPEN_WINDOW = win
        win.title("CS Probe - Character Select Mapper")
        win.geometry("1120x780")
        win.minsize(900, 600)
        win.configure(bg=_BG)

        status_var = tk.StringVar(value="Ready. Pick one target, zero it, watch the select screen, then restore it.")
        tip_var = tk.StringVar(value="Select a row to see what bucket it belongs to and what to watch for.")
        filter_var = tk.StringVar(value="Back panels")
        search_var = tk.StringVar(value="")
        list_count_var = tk.StringVar(value="")

        visible_indices: list[int] = []

        header = tk.Frame(win, bg=_BG, padx=12)
        header.pack(side="top", fill="x", pady=(10, 6))
        tk.Label(header, text="CS Probe", bg=_BG, fg=_TEXT, font=("Segoe UI", 14, "bold")).pack(side="left")
        tk.Label(header, text="character select RAM mapper", bg=_BG, fg=_MUTED, font=("Segoe UI", 9)).pack(side="left", padx=(12, 0))

        notes_shell = tk.Frame(win, bg=_BORDER, padx=1, pady=1)
        notes_shell.pack(side="top", fill="x", padx=12, pady=(0, 8))
        notes = tk.Label(
            notes_shell,
            text=KNOWN_SELECT_NOTES,
            bg=_PANEL,
            fg=_TEXT,
            anchor="w",
            justify="left",
            padx=10,
            pady=8,
            font=("Consolas", 9),
        )
        notes.pack(side="top", fill="x")

        controls_shell = tk.Frame(win, bg=_BORDER, padx=1, pady=1)
        controls_shell.pack(side="top", fill="x", padx=12, pady=(0, 8))
        controls = tk.Frame(controls_shell, bg=_PANEL, padx=10, pady=8)
        controls.pack(side="top", fill="x")

        def _selected_target_index() -> int | None:
            try:
                sel = listbox.curselection()
                if not sel:
                    return None
                pos = int(sel[0])
                if pos < 0 or pos >= len(visible_indices):
                    return None
                return int(visible_indices[pos])
            except Exception:
                return None

        def _set_status_from_result(res: dict[str, Any]) -> None:
            status_var.set(str(res.get("message") or res))
            refresh_rows(keep_selection=True)

        def _restore_all() -> None:
            _set_status_from_result(restore_all_probes(state, write_fn))

        def _zero_selected() -> None:
            idx = _selected_target_index()
            if idx is None:
                status_var.set("Pick a target row first.")
                return
            t = _target_at(state, idx)
            needs_confirm = (t.group in {"Risky roster records", "Selector ID map"}) or (t.group == "Trace results" and ("whole" in t.label.lower() or "selector id" in t.label.lower()))
            if needs_confirm and not messagebox.askyesno(
                "Risky target",
                f"Zero {t.label}? Restore quickly if the select screen acts weird.",
                parent=win,
            ):
                return
            _set_status_from_result(zero_probe_target(state, idx, read_fn, write_fn))

        def _restore_selected() -> None:
            idx = _selected_target_index()
            if idx is None:
                status_var.set("Pick a target row first.")
                return
            _set_status_from_result(restore_probe_target(state, idx, write_fn))

        def _zero_visible_group() -> None:
            group = filter_var.get()
            if group == "All":
                messagebox.showinfo("Choose a group", "Pick a group first. Group-zero on All is intentionally blocked.", parent=win)
                return
            if group in {"Trace results", "Selector ID map"}:
                messagebox.showinfo("One at a time", f"{group} targets are intentionally tested one row at a time.", parent=win)
                return
            if group == "Risky roster records" and not messagebox.askyesno(
                "Risky group",
                "Zero this risky roster group? This can break select flow until restored.",
                parent=win,
            ):
                return
            _set_status_from_result(zero_probe_group(state, group, read_fn, write_fn))

        def _restore_visible_group() -> None:
            group = filter_var.get()
            if group == "All":
                _restore_all()
                return
            _set_status_from_result(restore_probe_group(state, group, write_fn))

        def _trace_selected() -> None:
            idx = _selected_target_index()
            if idx is None:
                status_var.set("Pick a target row first.")
                return
            try:
                t = _target_at(state, idx)
                status_var.set(f"Tracing {t.label}; reading focused select-screen ranges...")
                win.update_idletasks()
                res = build_trace_targets_for(state, t, read_fn)
                filter_var.set("Trace results")
                _set_status_from_result(res)
            except Exception as e:
                status_var.set(f"Trace failed: {e!r}")

        def _clear_trace() -> None:
            _set_status_from_result(clear_trace_targets(state))

        _button(controls, "Zero Selected", _zero_selected, danger=True).pack(side="left", padx=(0, 8))
        _button(controls, "Restore Selected", _restore_selected).pack(side="left", padx=(0, 8))
        _button(controls, "Restore All", _restore_all).pack(side="left", padx=(0, 8))
        _button(controls, "Trace Selected", _trace_selected).pack(side="left", padx=(0, 8))
        _button(controls, "Clear Trace", _clear_trace).pack(side="left", padx=(0, 8))

        tk.Label(controls, text="Group", bg=_PANEL, fg=_MUTED).pack(side="left", padx=(12, 4))
        group_menu = tk.OptionMenu(controls, filter_var, "All", *GROUP_ORDER, command=lambda _v: refresh_rows())
        group_menu.configure(bg=_ACTIVE, fg=_TEXT, activebackground="#35466c", activeforeground=_TEXT, highlightthickness=1, highlightbackground=_BORDER)
        group_menu["menu"].configure(bg=_PANEL, fg=_TEXT, activebackground=_ACTIVE, activeforeground=_TEXT)
        group_menu.pack(side="left", padx=(0, 8))

        _button(controls, "Zero Group", _zero_visible_group, danger=True).pack(side="left", padx=(0, 8))
        _button(controls, "Restore Group", _restore_visible_group).pack(side="left", padx=(0, 8))

        tk.Label(controls, text="Search", bg=_PANEL, fg=_MUTED).pack(side="left", padx=(12, 4))
        search_entry = tk.Entry(controls, textvariable=search_var, bg="#0f1320", fg=_TEXT, insertbackground=_TEXT, relief="sunken", bd=1, width=20)
        search_entry.pack(side="left", padx=(0, 8))
        _button(controls, "Refresh", lambda: refresh_rows()).pack(side="left", padx=(0, 8))

        body_shell = tk.Frame(win, bg=_BORDER, padx=1, pady=1)
        body_shell.pack(side="top", fill="both", expand=True, padx=12, pady=(0, 8))
        body = tk.Frame(body_shell, bg=_PANEL)
        body.pack(side="top", fill="both", expand=True)

        top_line = tk.Frame(body, bg=_PANEL, padx=8, pady=6)
        top_line.pack(side="top", fill="x")
        tk.Label(top_line, textvariable=list_count_var, bg=_PANEL, fg=_OK, anchor="w", font=("Segoe UI", 10, "bold")).pack(side="left", fill="x", expand=True)
        tk.Label(top_line, text="Enter/double-click/Z: zero    R: restore    Trace Selected: follow a visual hit", bg=_PANEL, fg=_MUTED, anchor="e").pack(side="right")

        list_frame = tk.Frame(body, bg=_PANEL)
        list_frame.pack(side="top", fill="both", expand=True)
        listbox = tk.Listbox(
            list_frame,
            bg="#101522",
            fg=_TEXT,
            selectbackground=_ACTIVE,
            selectforeground=_TEXT,
            activestyle="none",
            font=("Consolas", 10),
            height=16,
            exportselection=False,
        )
        scroll = tk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)

        footer = tk.Frame(win, bg=_BG, padx=12)
        footer.pack(side="bottom", fill="x", pady=(0, 10))
        tk.Label(footer, textvariable=tip_var, bg=_PANEL, fg=_TEXT, anchor="w", justify="left", padx=10, pady=7, relief="ridge", bd=1, wraplength=1060).pack(side="top", fill="x", pady=(0, 6))
        tk.Label(footer, textvariable=status_var, bg=_BG, fg=_MUTED, anchor="w", justify="left").pack(fill="x")

        def _line_for(idx: int, t: SelectProbeTarget) -> str:
            saved = state.get("saved") or {}
            flag = "ZEROED" if _key_for(t) in saved else "      "
            if filter_var.get() == "All":
                return f"{flag}  {idx + 1:03d}  0x{t.addr:08X}  {t.size:3d}  {t.group:<22}  {t.label}"
            return f"{flag}  {idx + 1:03d}  0x{t.addr:08X}  {t.size:3d}  {t.label}"

        def _matches_search(idx: int, t: SelectProbeTarget, needle: str) -> bool:
            if not needle:
                return True
            hay = f"{idx + 1:03d} 0x{t.addr:08X} {t.size} {t.group} {t.label} {t.note} {_known_effect_for(t)}".lower()
            return needle.lower() in hay

        def refresh_rows(keep_selection: bool = False) -> None:
            prev_idx = _selected_target_index() if keep_selection else None
            visible_indices.clear()
            listbox.delete(0, "end")
            selected_group = filter_var.get()
            needle = search_var.get().strip()
            saved = state.get("saved") or {}

            for idx, t in enumerate(_targets(state)):
                if selected_group != "All" and t.group != selected_group:
                    continue
                if not _matches_search(idx, t, needle):
                    continue
                visible_indices.append(idx)
                listbox.insert("end", _line_for(idx, t))

            if prev_idx is not None and prev_idx in visible_indices:
                pos = visible_indices.index(prev_idx)
                listbox.selection_set(pos)
                listbox.see(pos)
            elif visible_indices:
                listbox.selection_set(0)

            group_hint = GROUP_HINTS.get(selected_group, "Showing all groups. Pick one group for less noise.")
            list_count_var.set(
                f"Modified: {len(saved)} / {_target_count(state)}   Trace: {len(_state_trace_targets(state))}   Showing: {len(visible_indices)}   Filter: {selected_group}"
            )
            tip_var.set(group_hint)
            update_detail_from_selection()

        def update_detail_from_selection(_event=None) -> None:
            idx = _selected_target_index()
            if idx is None:
                return
            t = _target_at(state, idx)
            effect = _known_effect_for(t)
            saved = state.get("saved") or {}
            active = "ZEROED" if _key_for(t) in saved else "not zeroed"
            tip_var.set(
                f"{idx + 1:03d} | {active} | {t.group} | 0x{t.addr:08X} len {t.size} | {t.label}"
                + (f" | {effect}" if effect else "")
                + (f" | note: {t.note}" if t.note else "")
            )

        def _on_key(event) -> str | None:
            key = str(getattr(event, "keysym", "")).lower()
            if key in {"return", "space", "z"}:
                _zero_selected()
                return "break"
            if key == "r":
                _restore_selected()
                return "break"
            if key == "t":
                _trace_selected()
                return "break"
            return None

        listbox.bind("<<ListboxSelect>>", update_detail_from_selection, add="+")
        listbox.bind("<Double-Button-1>", lambda _e: _zero_selected(), add="+")
        listbox.bind("<Return>", _on_key, add="+")
        listbox.bind("<space>", _on_key, add="+")
        listbox.bind("z", _on_key, add="+")
        listbox.bind("r", _on_key, add="+")
        listbox.bind("t", _on_key, add="+")
        search_var.trace_add("write", lambda *_args: refresh_rows())

        def on_close() -> None:
            global _OPEN_WINDOW
            _OPEN_WINDOW = None
            try:
                win.destroy()
            except Exception:
                pass

        win.protocol("WM_DELETE_WINDOW", on_close)
        refresh_rows()

    if tk_call is not None:
        tk_call(_show)
    else:
        root = tk.Tk()
        root.withdraw()
        _show(root)
        root.mainloop()
