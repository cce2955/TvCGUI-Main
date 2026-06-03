from __future__ import annotations

import re
import threading
from typing import Any

try:
    from dolphin_io import rbytes, wd32, wbytes
except Exception:  # pragma: no cover
    rbytes = None
    wd32 = None
    wbytes = None

_LOCK = threading.RLock()

# Source-side selector lane confirmed by staged select-screen dumps.
# Do not touch loader strings or 0x804E98A8/AC. The working lane is the
# select-wheel roster table, before chr/<tag> request rows are built.
ROSTER_TABLE_BASE = 0x809BD0C4

# Live wheel table captured from the staged hover/select dumps.
# Address = 0x809BD0C4 + slot * 4, value = character id.
ROSTER_SLOT_TABLE: tuple[tuple[int, int, str], ...] = (
    (0x00, 0x01, "Ken the Eagle"),
    (0x01, 0x08, "Jun the Swan"),
    (0x02, 0x02, "Casshan"),
    (0x03, 0x03, "Tekkaman"),
    (0x04, 0x04, "Polimar"),
    (0x05, 0x05, "Yatterman-1"),
    (0x06, 0x0A, "Karas"),
    (0x07, 0x06, "Doronjo"),
    (0x08, 0x07, "Ippatsuman"),
    (0x09, 0x0B, "Gold Lightan"),
    (0x0A, 0x1A, "Tekkaman Blade"),
    (0x0B, 0x1B, "Joe the Condor"),
    (0x0C, 0x1C, "Yatterman-2"),
    (0x0D, 0x63, "Random"),
    (0x0E, 0x1D, "Zero"),
    (0x0F, 0x1E, "Frank West"),
    (0x10, 0x16, "PTX-40A"),
    (0x11, 0x11, "Viewtiful Joe"),
    (0x12, 0x14, "Saki"),
    (0x13, 0x13, "Roll"),
    (0x14, 0x15, "Soki"),
    (0x15, 0x12, "Volnutt"),
    (0x16, 0x0E, "Batsu"),
    (0x17, 0x10, "Alex"),
    (0x18, 0x0F, "Morrigan"),
    (0x19, 0x0D, "Chun-Li"),
    (0x1A, 0x0C, "Ryu"),
    # Experimental appended logical clone slots. These do not replace visible slots;
    # they are written after the stock 0x00..0x1A table and require the count bump.
    (0x1B, 0x17, "Yami 1 clone"),
    (0x1C, 0x18, "Yami 2 clone"),
    (0x1D, 0x19, "Yami 3 clone"),
)

CHAR_ID_TO_NAME: dict[int, str] = {
    0x01: "Ken the Eagle",
    0x02: "Casshan",
    0x03: "Tekkaman",
    0x04: "Polimar",
    0x05: "Yatterman-1",
    0x06: "Doronjo",
    0x07: "Ippatsuman",
    0x08: "Jun the Swan",
    0x0A: "Karas",
    0x0B: "Gold Lightan",
    0x0C: "Ryu",
    0x0D: "Chun-Li",
    0x0E: "Batsu",
    0x0F: "Morrigan",
    0x10: "Alex",
    0x11: "Viewtiful Joe",
    0x12: "Volnutt",
    0x13: "Roll",
    0x14: "Saki",
    0x15: "Soki",
    0x16: "PTX-40A",
    # Hidden / non-wheel in-game entries requested for roster-table swizzle tests.
    # Decimal IDs 23, 24, 25 = hex 0x17, 0x18, 0x19.
    0x17: "Yami 1",
    0x18: "Yami 2",
    0x19: "Yami 3",
    0x1A: "Tekkaman Blade",
    0x1B: "Joe the Condor",
    0x1C: "Yatterman-2",
    0x1D: "Zero",
    0x1E: "Frank West",
    0x63: "Random",
}

ROSTER_SELECTOR_ADDRS: tuple[tuple[int, str], ...] = (
    (0x809BCEA0, "cursor index A"),
    (0x809BCF2C, "cursor index B"),
    (0x809BCF1C, "hover char id A"),
    (0x809BCFC0, "hover char id B"),
    (0x809BD090, "selected/locked char id A"),
    (0x809BD098, "selected/locked or pending char id B"),
)

# Experimental logical append points for real extra Yami slots.
# The staged dumps show 0x809BD0C0 holds 0x1B, matching the 27 stock wheel slots.
# Writing 0x1E here and in the mirrored selector count fields is the first safe
# test for whether the wheel can walk past Ryu into slots 0x1B..0x1D.
ROSTER_COUNT_ADDRS: tuple[tuple[int, str], ...] = (
    (0x809BCEA4, "selector count A"),
    (0x809BCF3C, "selector count B"),
    (0x809BD0C0, "roster table count"),
)

YAMI_CLONE_SLOTS: tuple[tuple[int, int, str], ...] = (
    (0x1B, 0x17, "Yami 1"),
    (0x1C, 0x18, "Yami 2"),
    (0x1D, 0x19, "Yami 3"),
)

YAMI_CLONE_COUNT = 0x1E

# Experimental visual/icon-shell aliases. The hidden Yami shell appears to
# resolve through the loaded silhouette labels. The roster table still supplies
# the real Yami character ID; these aliases only affect the select-screen shell
# label/icon label used by the hidden slot.
#
# The select/name string table uses compact 0x10-byte entries here where the
# last byte is the string length. We write full entry-sized records and restore
# the originals.
VISUAL_ALIAS_ENTRY_SIZE = 0x10
VISUAL_ALIAS_SELECT_ADDR = 0x930DEF50
VISUAL_ALIAS_NAME_ADDR = 0x930DE9E4
VISUAL_ALIAS_EXPECT_SELECT = b"select_sil"
VISUAL_ALIAS_EXPECT_NAME = b"name_sil"
VISUAL_ALIAS_PRESETS: dict[str, tuple[str, tuple[tuple[int, bytes, bytes, str], ...]]] = {
    "zero": (
        "Zero shell",
        (
            (VISUAL_ALIAS_SELECT_ADDR, VISUAL_ALIAS_EXPECT_SELECT, b"select_zer", "select_sil -> select_zer"),
            (VISUAL_ALIAS_NAME_ADDR, VISUAL_ALIAS_EXPECT_NAME, b"name_zer", "name_sil -> name_zer"),
        ),
    ),
    "random": (
        "Random select icon",
        (
            (VISUAL_ALIAS_SELECT_ADDR, VISUAL_ALIAS_EXPECT_SELECT, b"select_random0", "select_sil -> select_random0"),
            (VISUAL_ALIAS_NAME_ADDR, VISUAL_ALIAS_EXPECT_NAME, b"name_zer", "name_sil -> name_zer"),
        ),
    ),
    "random_icon": (
        "Random icon texture",
        (
            (VISUAL_ALIAS_SELECT_ADDR, VISUAL_ALIAS_EXPECT_SELECT, b"icon_random0", "select_sil -> icon_random0"),
            (VISUAL_ALIAS_NAME_ADDR, VISUAL_ALIAS_EXPECT_NAME, b"name_zer", "name_sil -> name_zer"),
        ),
    ),
    "cmn": (
        "CMN icon",
        (
            (VISUAL_ALIAS_SELECT_ADDR, VISUAL_ALIAS_EXPECT_SELECT, b"icon_cmn", "select_sil -> icon_cmn"),
            (VISUAL_ALIAS_NAME_ADDR, VISUAL_ALIAS_EXPECT_NAME, b"name_zer", "name_sil -> name_zer"),
        ),
    ),
}


# Wheel thumbnail icon source probes. The previous visual alias writes changed the
# select/name shell labels, but the wheel thumbnail is a separate icon/material
# binding. These probes patch actual icon string-table entries. They are still
# reversible and intentionally scoped so we can tell whether the new Yami shell
# is using the hidden Yami icon entries or stealing the nearest neighbor icon.
ICON_ALIAS_ENTRY_SIZE = 0x10
ICON_TABLE_ENTRY_ADDRS: dict[str, tuple[int, ...]] = {
    "cmn": (0x92D38200, 0x932FC378),
    "gac": (0x92D38230, 0x932FC3A8),
    "ryu": (0x92D382F0, 0x932FC468),
    "zer": (0x92D383B0, 0x932FC528),
    "fra": (0x92D38220, 0x932FC398),
    "tk1": (0x92D38320, 0x932FC498),
    "tk2": (0x92D38330, 0x932FC4A8),
    "tk3": (0x92D38340, 0x932FC4B8),
}
ICON_ALIAS_TARGETS: dict[str, tuple[str, bytes]] = {
    "cmn": ("CMN icon", b"icon_cmn"),
    "zero": ("Zero icon", b"icon_zer"),
    "ken": ("Ken icon", b"icon_gac"),
    "ryu": ("Ryu icon", b"icon_ryu"),
    "yami1": ("Yami 1 native icon", b"icon_tk1"),
    "yami2": ("Yami 2 native icon", b"icon_tk2"),
    "yami3": ("Yami 3 native icon", b"icon_tk3"),
    "random0": ("Random0 icon", b"icon_random0"),
}
ICON_ALIAS_SCOPES: dict[str, tuple[str, tuple[str, ...]]] = {
    "active_shell": ("Live Yami shell fallback strings", ()),
    "yami": ("Yami native icon entries only", ("tk1", "tk2", "tk3")),
    "neighbors": ("Likely nearest-neighbor visible icon entries", ("gac", "ryu", "zer", "fra")),
    "ken": ("Ken/Gatchaman icon entry only", ("gac",)),
    "ryu": ("Ryu icon entry only", ("ryu",)),
    "zero": ("Zero icon entry only", ("zer",)),
    "frank": ("Frank icon entry only", ("fra",)),
}

# The dumped active shell code still contains yami-specific fallback strings like
# select_ / select_gac and select_ / select_chu. Patching the static icon_*
# tables does not affect these live fallback records, which is why the new Yami
# slot visually borrows the nearest existing thumbnail. These addresses are the
# active yami-branch fallback fields captured from the current select-screen dump.
ACTIVE_SHELL_STRING_FIELD_SIZE = 0x0C
ACTIVE_SHELL_SELECT_FIELDS: tuple[int, ...] = (
    0x90821EE0, 0x90821F58, 0x90821F64,
    0x908220D8, 0x90822150, 0x9082215C,
    0x908222E0, 0x90822358, 0x90822364,
    0x908224F8, 0x90822570, 0x9082257C,
)
ACTIVE_SHELL_NAME_FIELDS: tuple[int, ...] = (
    0x90821F10, 0x90821F9C,
    0x90822108, 0x9082219C,
    0x90822310, 0x9082239C,
    0x90822528, 0x908225BC,
)
ACTIVE_SHELL_SELECT_TARGETS: dict[str, tuple[str, bytes]] = {
    "cmn": ("CMN icon via live shell", b"icon_cmn"),
    "zero": ("Zero select via live shell", b"select_zer"),
    "ken": ("Ken select via live shell", b"select_gac"),
    "ryu": ("Ryu select via live shell", b"select_ryu"),
    "yami1": ("Yami 1 icon via live shell", b"icon_tk1"),
    "yami2": ("Yami 2 icon via live shell", b"icon_tk2"),
    "yami3": ("Yami 3 icon via live shell", b"icon_tk3"),
    # Full select_random0/icon_random0 does not fit in this 12-byte live field.
    # random0 is a short probe and may or may not resolve.
    "random0": ("Random short probe via live shell", b"random0"),
}



# Borrowed thumbnail/name assets for the three Yami forms. Yami appears to have
# valid character/resource IDs but no usable select-wheel thumbnail of its own,
# so this aliases the three native hidden icon labels to visible stock icons.
# Chosen mapping requested for testing:
#   Yami 1 / tk1 -> Yatterman-2 / ya2
#   Yami 2 / tk2 -> Tekkaman / tek
#   Yami 3 / tk3 -> Casshan / cas


# Bottom carousel thumbnail pane probe. The screenshots show that select/name
# shell aliases and selected-id mirror sync do not control the close-up bottom
# wheel thumbnail. These live records contain paired select_random*/icon_ fields
# in the active select-screen layout. The blank icon_ fields are the next likely
# source for the neighbor/fallback thumbnails. They are 8-byte string slots:
# b"icon_\0\0\0" can be replaced by b"icon_tek", b"icon_cas", b"icon_ya2".
BOTTOM_ICON_FIELD_SIZE = 0x08
BOTTOM_BLANK_ICON_FIELDS: tuple[int, ...] = (
    0x9083B2A0, 0x9083B2E8,
    0x9083CBF0, 0x9083CCBC, 0x9083CE58, 0x9083CF24,
    0x9083D0C0, 0x9083D18C, 0x9083D328, 0x9083D3F4,
)
BOTTOM_LATE_BLANK_ICON_FIELDS: tuple[int, ...] = (
    0x9083CBF0, 0x9083CCBC, 0x9083CE58, 0x9083CF24,
    0x9083D0C0, 0x9083D18C, 0x9083D328, 0x9083D3F4,
)
BOTTOM_ICON_EXPECT_BLANK = b"icon_\x00\x00\x00"
BOTTOM_ICON_TARGETS: dict[str, tuple[str, bytes]] = {
    "ya2": ("Yatterman-2 bottom icon", b"icon_ya2"),
    "tek": ("Tekkaman bottom icon", b"icon_tek"),
    "cas": ("Casshan bottom icon", b"icon_cas"),
    "ryu": ("Ryu bottom icon", b"icon_ryu"),
    "zer": ("Zero bottom icon", b"icon_zer"),
    "gac": ("Ken bottom icon", b"icon_gac"),
}
BOTTOM_ICON_SCOPES: dict[str, tuple[str, tuple[int, ...]]] = {
    "bottom_all_blanks": ("Bottom carousel all blank icon fields", BOTTOM_BLANK_ICON_FIELDS),
    "bottom_late_blanks": ("Bottom carousel late blank icon fields", BOTTOM_LATE_BLANK_ICON_FIELDS),
    "bottom_first_pair": ("Bottom carousel first blank pair", BOTTOM_BLANK_ICON_FIELDS[:2]),
}
BOTTOM_YAMI_TRIO_PLAN: tuple[tuple[int, bytes, str], ...] = (
    (0x9083B2A0, b"icon_ya2", "bottom blank 0 -> Yatterman-2"),
    (0x9083B2E8, b"icon_tek", "bottom blank 1 -> Tekkaman"),
    (0x9083CBF0, b"icon_cas", "bottom blank 2 -> Casshan"),
    (0x9083CCBC, b"icon_ya2", "bottom blank 3 -> Yatterman-2"),
    (0x9083CE58, b"icon_tek", "bottom blank 4 -> Tekkaman"),
    (0x9083CF24, b"icon_cas", "bottom blank 5 -> Casshan"),
    (0x9083D0C0, b"icon_ya2", "bottom blank 6 -> Yatterman-2"),
    (0x9083D18C, b"icon_tek", "bottom blank 7 -> Tekkaman"),
    (0x9083D328, b"icon_cas", "bottom blank 8 -> Casshan"),
    (0x9083D3F4, b"icon_ya2", "bottom blank 9 -> Yatterman-2"),
)


# Material-layer probe. The close-up bottom wheel did not respond to icon_* or
# select_* string aliases. The 20260603_085843/085927 dumps show active material
# swap fields using mof_* names around 0x9081DBxx and 0x9083A3xx. These fields
# fit the donor names exactly (8 bytes including the NUL). This is still a lab
# probe: write, observe, then Restore roster.
MATERIAL_FIELD_SIZE = 0x08
MATERIAL_ALIAS_TARGETS: dict[str, tuple[str, bytes]] = {
    "ya2": ("Yatterman-2 material", b"mof_ya2"),
    "tek": ("Tekkaman material", b"mof_tek"),
    "cas": ("Casshan material", b"mof_cas"),
    "ryu": ("Ryu material", b"mof_ryu"),
    "gac": ("Ken material", b"mof_gac"),
    "chu": ("Chun-Li material", b"mof_chu"),
    "zer": ("Zero material", b"mof_zer"),
}

# Live selected/top-card material fields. 0x90818460 flips with the active
# character in the dumps, while 0x90818468 is often a blank fallback material.
MATERIAL_SELECTED_FIELDS: tuple[int, ...] = (
    0x90818460, 0x90818468,
)

# Bottom wheel material-script fields near the close-up carousel records. These
# are the first material-level candidates after icon_* probes failed.
MATERIAL_BOTTOM_CORE_FIELDS: tuple[int, ...] = (
    0x9083A3D8, 0x9083A3E0, 0x9083A3F8, 0x9083A400,
    0x9083A418, 0x9083A420, 0x9083A438, 0x9083A440,
    0x9083A684, 0x9083A68C, 0x9083A6A4, 0x9083A6AC,
    0x9083A6C4, 0x9083A6CC,
)

# Blank/fallback material fields that appear in repeating carousel material
# records. These are likely where the hidden shell falls back before borrowing a
# neighbor material.
MATERIAL_BLANK_FIELDS: tuple[int, ...] = (
    0x9081E6F4, 0x9081E728, 0x9081E7B4, 0x9081E7E8,
    0x9081E8DC, 0x9081E910, 0x9081E998, 0x9081E9CC,
    0x90818468,
)

# Wider Ryu material set from the active select-screen scripts. Use this only as
# a strong probe because it can visibly recolor/retarget stock Ryu material use.
MATERIAL_RYU_FIELDS: tuple[int, ...] = (
    0x90818460,
    0x9081DB80, 0x9081DBC0, 0x9081DC6C, 0x9081DC8C,
    0x9081DC94, 0x9081DCAC, 0x9081DCCC, 0x9081DCD4,
    0x9081DDB4, 0x9081DDF4, 0x9081DE80, 0x9081DEA0,
    0x9081DEA8, 0x9081DEC0, 0x9081DEE0, 0x9081DEE8,
    0x9081E730, 0x9081E7F0, 0x9081E918, 0x9081E9D4,
    0x9083A400, 0x9083A440, 0x9083A684, 0x9083A6A4,
    0x9083A6AC, 0x9083A6CC,
)

MATERIAL_ALIAS_SCOPES: dict[str, tuple[str, tuple[int, ...]]] = {
    "material_selected": ("Selected/top-card live material fields", MATERIAL_SELECTED_FIELDS),
    "material_bottom_core": ("Bottom carousel core material fields", MATERIAL_BOTTOM_CORE_FIELDS),
    "material_blanks": ("Blank/fallback live material fields", MATERIAL_BLANK_FIELDS),
    "material_ryu": ("All live Ryu material fields", MATERIAL_RYU_FIELDS),
    "material_all_live": ("Selected + bottom + blank material fields", tuple(dict.fromkeys(MATERIAL_SELECTED_FIELDS + MATERIAL_BOTTOM_CORE_FIELDS + MATERIAL_BLANK_FIELDS))),
}

MATERIAL_YAMI_TRIO_PLAN: tuple[tuple[int, bytes, str], ...] = (
    # hidden/fallback material records: cycle Yami 1/2/3 donor materials
    (0x9081E6F4, b"mof_ya2", "hidden material 0 -> Yatterman-2"),
    (0x9081E728, b"mof_tek", "hidden material 1 -> Tekkaman"),
    (0x9081E7B4, b"mof_cas", "hidden material 2 -> Casshan"),
    (0x9081E7E8, b"mof_ya2", "hidden material 3 -> Yatterman-2"),
    (0x9081E8DC, b"mof_tek", "hidden material 4 -> Tekkaman"),
    (0x9081E910, b"mof_cas", "hidden material 5 -> Casshan"),
    (0x9081E998, b"mof_ya2", "hidden material 6 -> Yatterman-2"),
    (0x9081E9CC, b"mof_tek", "hidden material 7 -> Tekkaman"),
    # bottom carousel core material records near the visible ring
    (0x9083A3D8, b"mof_ya2", "bottom core 0 -> Yatterman-2"),
    (0x9083A400, b"mof_tek", "bottom core 1 -> Tekkaman"),
    (0x9083A440, b"mof_cas", "bottom core 2 -> Casshan"),
    (0x9083A684, b"mof_ya2", "bottom core 3 -> Yatterman-2"),
    (0x9083A6A4, b"mof_tek", "bottom core 4 -> Tekkaman"),
    (0x9083A6CC, b"mof_cas", "bottom core 5 -> Casshan"),
)

# 0300 portrait/resource path probe. The material probe wrote successfully, but the
# close-up wheel thumbnail still stayed on the physical Ryu/neighbor icon. The next
# layer is the loaded chr/<tag>/0300.brres portrait path table. Hidden Yami tags are
# tk1/tk2/tk3; this aliases only their 0300 portrait resources to visible donors.
# It deliberately does not touch chr/tk*/0000.brres, so the playable Yami body stays
# Yami while the select-wheel portrait can borrow another character's icon resource.
RESOURCE_0300_FIELD_SIZE = len(b"chr/tk1/0300.brres")
RESOURCE_0300_YAMI_TRIO_PLAN: tuple[tuple[int, bytes, bytes, str], ...] = (
    # Yami 1 tk1 -> Yatterman-2 ya2
    (0x90826404, b"chr/tk1/0300.brres", b"chr/ya2/0300.brres", "0300 table A tk1 -> ya2"),
    (0x9082AE34, b"chr/tk1/0300.brres", b"chr/ya2/0300.brres", "0300 table B tk1 -> ya2"),
    (0x9082FFEC, b"chr/tk1/0300.brres", b"chr/ya2/0300.brres", "0300 table C tk1 -> ya2"),
    (0x90834A20, b"chr/tk1/0300.brres", b"chr/ya2/0300.brres", "0300 table D tk1 -> ya2"),
    # Yami 2 tk2 -> Tekkaman tek
    (0x90826438, b"chr/tk2/0300.brres", b"chr/tek/0300.brres", "0300 table A tk2 -> tek"),
    (0x9082AE68, b"chr/tk2/0300.brres", b"chr/tek/0300.brres", "0300 table B tk2 -> tek"),
    (0x90830020, b"chr/tk2/0300.brres", b"chr/tek/0300.brres", "0300 table C tk2 -> tek"),
    (0x90834A54, b"chr/tk2/0300.brres", b"chr/tek/0300.brres", "0300 table D tk2 -> tek"),
    # Yami 3 tk3 -> Casshan cas
    (0x9082646C, b"chr/tk3/0300.brres", b"chr/cas/0300.brres", "0300 table A tk3 -> cas"),
    (0x9082AE9C, b"chr/tk3/0300.brres", b"chr/cas/0300.brres", "0300 table B tk3 -> cas"),
    (0x90830054, b"chr/tk3/0300.brres", b"chr/cas/0300.brres", "0300 table C tk3 -> cas"),
    (0x90834A88, b"chr/tk3/0300.brres", b"chr/cas/0300.brres", "0300 table D tk3 -> cas"),
)

BORROWED_YAMI_ICON_PLAN: tuple[dict[str, Any], ...] = (
    {
        "yami_key": "tk1",
        "yami_label": "Yami 1",
        "donor_key": "ya2",
        "donor_label": "Yatterman-2",
        "icon": b"icon_ya2",
        "select": b"select_ya2",
        "name": b"name_ya2",
    },
    {
        "yami_key": "tk2",
        "yami_label": "Yami 2",
        "donor_key": "tek",
        "donor_label": "Tekkaman",
        "icon": b"icon_tek",
        "select": b"select_tek",
        "name": b"name_tek",
    },
    {
        "yami_key": "tk3",
        "yami_label": "Yami 3",
        "donor_key": "cas",
        "donor_label": "Casshan",
        "icon": b"icon_cas",
        "select": b"select_cas",
        "name": b"name_cas",
    },
)

# Best-effort groups of live hidden-shell select fallback records seen in the
# 20260603 select-screen dumps. These are not the character source; they are only
# visual fallback strings for the select plate. We patch them in groups so future
# Yami shells can use different borrowed select plates instead of all borrowing
# the same neighbor/last icon.
ACTIVE_SHELL_SELECT_GROUPS: tuple[tuple[int, ...], ...] = (
    (0x90821EE0, 0x90821F58, 0x90821F64),
    (0x908220D8, 0x90822150, 0x9082215C),
    (0x908222E0, 0x90822358, 0x90822364),
    (0x908224F8, 0x90822570, 0x9082257C),
)



# Loaded close-up face resource probe. The bottom/close-up Ryu-looking icon did
# not respond to icon_*, select_*, mof_* or chr/<tag>/0300.brres string probes.
# The newer dump shows the active close-up portrait package has loaded BRLYT/TPL
# resource names like face_ryu.brlyt, face_ryu.tpl, face_sita_ryu.tpl and
# Name_ryu.tpl. This probe patches that loaded face-resource name layer.
# Use loaded donors first (gac/chu/jun) because they are visible in the dump.
FACE_RESOURCE_FIELD_PLAN: tuple[tuple[int, bytes, str], ...] = (
    (0x921FE7E2, b"face_ryu.brlyt", "face layout"),
    (0x921FE7F6, b"face_ryu.tpl", "face texture"),
    (0x921FE803, b"face_ryu_r.tpl", "face red texture"),
    (0x921FE812, b"face_sita_ryu.tpl", "small face texture"),
    (0x921FE824, b"face_sita_ryu_r.tpl", "small face red texture"),
    (0x921FE838, b"Name_ryu.tpl", "name texture"),
)
FACE_RESOURCE_DONORS: dict[str, tuple[str, tuple[bytes, bytes, bytes, bytes, bytes, bytes]]] = {
    "gac": ("Ken/Gatchaman loaded face", (b"face_gac.brlyt", b"face_gac.tpl", b"face_gac_r.tpl", b"face_sita_gac.tpl", b"face_sita_gac_r.tpl", b"Name_gac.tpl")),
    "chu": ("Chun-Li loaded face", (b"face_chu.brlyt", b"face_chu.tpl", b"face_chu_r.tpl", b"face_sita_chu.tpl", b"face_sita_chu_r.tpl", b"Name_chu.tpl")),
    "jun": ("Jun loaded face", (b"face_jun.brlyt", b"face_jun.tpl", b"face_jun_r.tpl", b"face_sita_jun.tpl", b"face_sita_jun_r.tpl", b"Name_jun.tpl")),
    # Requested/unloaded donor names. These are useful probes, but if the resource
    # was not loaded yet, the draw layer may keep using Ryu until we hook earlier.
    "fra": ("Frank West face names", (b"face_fra.brlyt", b"face_fra.tpl", b"face_fra_r.tpl", b"face_sita_fra.tpl", b"face_sita_fra_r.tpl", b"Name_fra.tpl")),
    "tkb": ("Tekkaman Blade face names", (b"face_tkb.brlyt", b"face_tkb.tpl", b"face_tkb_r.tpl", b"face_sita_tkb.tpl", b"face_sita_tkb_r.tpl", b"Name_tkb.tpl")),
    "ya2": ("Yatterman-2 face names", (b"face_ya2.brlyt", b"face_ya2.tpl", b"face_ya2_r.tpl", b"face_sita_ya2.tpl", b"face_sita_ya2_r.tpl", b"Name_ya2.tpl")),
    "tek": ("Tekkaman face names", (b"face_tek.brlyt", b"face_tek.tpl", b"face_tek_r.tpl", b"face_sita_tek.tpl", b"face_sita_tek_r.tpl", b"Name_tek.tpl")),
    "cas": ("Casshan face names", (b"face_cas.brlyt", b"face_cas.tpl", b"face_cas_r.tpl", b"face_sita_cas.tpl", b"face_sita_cas_r.tpl", b"Name_cas.tpl")),
}

# Loaded face block copy probe. The string-name probe was too shallow: the
# current dumps show actual heap blocks containing face_ryu/face_gac/face_chu
# BRLYT/TPL archives. This copies a loaded donor archive payload over the active
# Ryu face archive payload without touching the heap header. It is reversible.
# Use loaded donors only first; unloaded far donors do not have source bytes.
FACE_BLOCK_ACTIVE_RYU_PAYLOAD_ADDR = 0x921FE740
FACE_BLOCK_ACTIVE_RYU_PAYLOAD_SIZE = 0x3A00
FACE_BLOCK_COPY_DONORS: dict[str, tuple[str, int, int]] = {
    "gac": ("Ken/Gatchaman loaded face block", 0x92202160, 0x3020),
    "chu": ("Chun-Li loaded face block", 0x922051A0, 0x2F20),
}

PANE_PROBE_RECORD_ADDRS: tuple[int, ...] = (
    0x90818440,
    0x90844A00,
    0x90844700,
    0x90847740,
    0x90847D60,
    0x921FE720,
    0x92202140,
    0x92205180,
)



# Ken-duplicate visual probe. The 20260603_111404 dump has the real Yami
# hover fields active, but the live visual bank still says mof_gac/gac.
# Do not touch the central active bank at 0x90818460/0x90818470 here because
# direct writes there froze the cursor. These are the duplicate material records
# around the carousel scripts only.
KEN_DUP_SAFE_MOF_FIELDS: tuple[int, ...] = (
    0x9081DB58, 0x9081DB78, 0x9081DB98, 0x9081DBB8,
    0x9081DD8C, 0x9081DDAC, 0x9081DDCC, 0x9081DDEC,
    0x9083A3D8, 0x9083A3F8, 0x9083A418, 0x9083A438,
)
KEN_DUP_SAFE_SELECT_FIELDS: tuple[int, ...] = (
    0x9081B440, 0x9081B490,
)
KEN_DUP_SAFE_NAME_FIELDS: tuple[int, ...] = (
    0x9081B4E0, 0x9081B530, 0x90820EA0, 0x90822190, 0x908225B0,
)
KEN_DUP_STATIC_ICON_FIELDS: tuple[int, ...] = (
    0x92D38230, 0x932FC3A8,
)
KEN_DUP_EXPECT_MOF = b"mof_gac"
KEN_DUP_EXPECT_SELECT = b"select_gac"
KEN_DUP_EXPECT_NAME = b"name_gac"
KEN_DUP_EXPECT_ICON = b"icon_gac"
KEN_DUP_FRANK_MOF = b"mof_fra"
KEN_DUP_FRANK_SELECT = b"select_fra"
KEN_DUP_FRANK_NAME = b"name_fra"
KEN_DUP_FRANK_ICON = b"icon_fra"

# Owner pointers observed near the central visual bank. These are not the bytes
# inside the active bank; they are the pane/script owner references into it.
KEN_DUP_OWNER_MOF_PTR = 0x90844A18
KEN_DUP_OWNER_TAG_PTR = 0x90844A1C
KEN_DUP_FRANK_STATIC_MOF_ADDR = 0x930DE7AC  # mof_fra
KEN_DUP_FRANK_STATIC_TAG_ADDR = 0x930DE7B0  # fra

# Cursor/hover mirrors. Force-hover is intentionally separate from installing
# clone table/count because it is a stronger live-state nudge.
CURSOR_INDEX_ADDRS: tuple[int, ...] = (0x809BCEA0, 0x809BCF2C)
HOVER_CHAR_ID_ADDRS: tuple[int, ...] = (0x809BCF1C, 0x809BCFC0)
# Preview/selected mirrors observed in staged dumps. Stock slots update these,
# but the appended Yami slots can hover while leaving 0x809BD090 stuck on the
# previous stock character, which makes the select-wheel thumbnail appear to
# fall back to Ryu/nearest neighbor. This probe syncs those mirrors to the
# appended Yami ID.
FOCUS_CHAR_ID_ADDRS: tuple[int, ...] = (0x809BD090, 0x809BD098)

_SLOT_TO_ID = {slot: cid for slot, cid, _name in ROSTER_SLOT_TABLE}
_SLOT_TO_DEFAULT_NAME = {slot: name for slot, _cid, name in ROSTER_SLOT_TABLE}
_NAME_TO_ID = {name.lower(): cid for cid, name in CHAR_ID_TO_NAME.items()}
_NAME_TO_SLOT = {name.lower(): slot for slot, _cid, name in ROSTER_SLOT_TABLE}

_ROSTER_QUEUE: list[dict[str, Any]] = []
_ROSTER_ORIGINALS: dict[int, int] = {}
_ROSTER_BYTE_ORIGINALS: dict[int, bytes] = {}


# Strong far-donor probe for the close-up/select face problem.
# The 085843 vs 085927 dumps showed the obvious live switch at:
#   0x90818460: mof_alx -> mof_ryu
#   0x90818470: alx     -> ryu
# The previous material probe only changed mof_* fields. This one also changes
# the short active tag and a few live shell/icon/material/name fields to a far
# donor so the result is visually obvious. This is still a probe: Yami IDs stay
# in the roster table.
ACTIVE_SELECTED_MATERIAL_FIELDS: tuple[int, ...] = (0x90818460, 0x90818468)
ACTIVE_SELECTED_TAG_FIELDS: tuple[int, ...] = (0x90818470,)
ACTIVE_SELECTED_SELECT_FIELDS: tuple[int, ...] = (0x90818478,)
ACTIVE_SELECTED_NAME_FIELDS: tuple[int, ...] = (0x90818484,)

FAR_DONOR_TARGETS: dict[str, dict[str, Any]] = {
    "fra": {
        "label": "Frank West",
        "tag": b"fra",
        "material": b"mof_fra",
        "icon": b"icon_fra",
        "select": b"select_fra",
        "name": b"name_fra",
        "face": (b"face_fra.brlyt", b"face_fra.tpl", b"face_fra_r.tpl", b"face_sita_fra.tpl", b"face_sita_fra_r.tpl", b"Name_fra.tpl"),
        "0300": b"chr/fra/0300.brres",
    },
    "tkb": {
        "label": "Tekkaman Blade",
        "tag": b"tkb",
        "material": b"mof_tkb",
        "icon": b"icon_tkb",
        "select": b"select_tkb",
        "name": b"name_tkb",
        "face": (b"face_tkb.brlyt", b"face_tkb.tpl", b"face_tkb_r.tpl", b"face_sita_tkb.tpl", b"face_sita_tkb_r.tpl", b"Name_tkb.tpl"),
        "0300": b"chr/tkb/0300.brres",
    },
    "ya2": {
        "label": "Yatterman-2",
        "tag": b"ya2",
        "material": b"mof_ya2",
        "icon": b"icon_ya2",
        "select": b"select_ya2",
        "name": b"name_ya2",
        "face": (b"face_ya2.brlyt", b"face_ya2.tpl", b"face_ya2_r.tpl", b"face_sita_ya2.tpl", b"face_sita_ya2_r.tpl", b"Name_ya2.tpl"),
        "0300": b"chr/ya2/0300.brres",
    },
    "tek": {
        "label": "Tekkaman",
        "tag": b"tek",
        "material": b"mof_tek",
        "icon": b"icon_tek",
        "select": b"select_tek",
        "name": b"name_tek",
        "face": (b"face_tek.brlyt", b"face_tek.tpl", b"face_tek_r.tpl", b"face_sita_tek.tpl", b"face_sita_tek_r.tpl", b"Name_tek.tpl"),
        "0300": b"chr/tek/0300.brres",
    },
}

# Frank face-lock owner/pane probe.
# User goal: Yami IDs stay 0x17/0x18/0x19, but the close-up/wheel face should
# stop resolving to the neighbor Ryu/Ken pane and instead point at one obvious
# donor face. The unsafe test wrote into 0x90818460/0x90818470 and froze cursor
# motion. This version does NOT overwrite that live output bank. It rewires the
# owner record that points at it.
FRANK_FACE_LOCK_TAG_ADDR = 0x930DE7B0       # substring "fra\0" inside static mof_fra entry
FRANK_FACE_LOCK_MOF_ADDR = 0x930DE7AC       # static "mof_fra\0"
FRANK_FACE_OWNER_MOF_PTR = 0x90844A18       # normally -> 0x90818468, prefix/material source
FRANK_FACE_OWNER_TAG_PTR = 0x90844A1C       # normally -> 0x90818470, live selected tag

FRANK_FACE_LOCK_POINTER_TAG_ONLY: tuple[tuple[int, int, str], ...] = (
    (FRANK_FACE_OWNER_TAG_PTR, FRANK_FACE_LOCK_TAG_ADDR, "owner tag pointer -> static fra"),
)
FRANK_FACE_LOCK_POINTER_PAIR: tuple[tuple[int, int, str], ...] = (
    (FRANK_FACE_OWNER_MOF_PTR, FRANK_FACE_LOCK_MOF_ADDR, "owner material pointer -> static mof_fra"),
    (FRANK_FACE_OWNER_TAG_PTR, FRANK_FACE_LOCK_TAG_ADDR, "owner tag pointer -> static fra"),
)

FRANK_FACE_LOCK_ICON_KEYS: tuple[str, ...] = ("tk1", "tk2", "tk3")
FRANK_FACE_LOCK_FACE_NAME_PLAN: tuple[tuple[int, bytes, bytes, str], ...] = (
    (0x921FE7E2, b"face_ryu.brlyt", b"face_fra.brlyt", "loaded face layout name -> Frank"),
    (0x921FE7F6, b"face_ryu.tpl", b"face_fra.tpl", "loaded face texture name -> Frank"),
    (0x921FE803, b"face_ryu_r.tpl", b"face_fra_r.tpl", "loaded face red texture name -> Frank"),
    (0x921FE812, b"face_sita_ryu.tpl", b"face_sita_fra.tpl", "loaded small face texture name -> Frank"),
    (0x921FE824, b"face_sita_ryu_r.tpl", b"face_sita_fra_r.tpl", "loaded small face red texture name -> Frank"),
    (0x921FE838, b"Name_ryu.tpl", b"Name_fra.tpl", "loaded name plate texture name -> Frank"),
)

_ROSTER_STATE: dict[str, Any] = {
    "last_error": "",
    "last_action": "",
    "last_snapshot": {},
    "queued": 0,
    "patches": 0,
    "restored": 0,
    "failed": 0,
    "restore_available": False,
    "clone_table_installed": False,
    "clone_count_installed": False,
    "last_clone_slot": "",
    "visual_alias_installed": False,
    "visual_alias_mode": "",
    "icon_alias_installed": False,
    "icon_alias_scope": "",
    "icon_alias_target": "",
    "byte_restore_available": False,
    "face_block_copy_installed": False,
    "face_block_copy_donor": "",
    "pane_probe_snapshot": {},
    "frank_face_lock_installed": False,
    "frank_face_lock_mode": "",
    "ken_dupe_patch_installed": False,
    "ken_dupe_patch_mode": "",
    "owned_bank_installed": False,
    "owned_bank_addr": "",
    "owned_bank_snapshot": {},
    "visual_table_snapshot": {},
    "visual_table_patch_installed": False,
    "visual_table_patch_mode": "",
    "extra_characters_enabled": False,
    "extra_characters_requested": False,
    "extra_characters_mode": "",
    "extra_characters_select_active": False,
    "extra_characters_patch_present": False,
    "extra_characters_guard": "",
    "solo_team_enabled": False,
    "solo_team_requested": False,
    "solo_team_mode": "",
    "solo_team_guard": "",
}

_INT_RE = re.compile(r"0x[0-9a-fA-F]+|\b\d+\b")

# The roster/profile patch is only safe while the character-select wheel is
# actually resident. Outside character select these same addresses are reused
# for pointers/state. The 20260603_121441 dump proved 0x809BD0C0 can be a
# pointer (0x809BDC40), not the roster count, so the automated toggle must be
# armed and guarded instead of blindly writing.
SELECT_SCREEN_STOCK_COUNT = 0x1B
SELECT_SCREEN_PATCHED_COUNT = 0x1E
# 0x1C shows up after the game rebuilds character select with the profile table
# append still resident. It is still a real select-screen roster/count state;
# the old guard rejected it, so Extra chars stayed armed but never re-applied
# after leaving and returning to the select screen.
SELECT_SCREEN_INTERMEDIATE_COUNT = 0x1C
SELECT_SCREEN_COUNT_VALUES = {
    SELECT_SCREEN_STOCK_COUNT,
    SELECT_SCREEN_INTERMEDIATE_COUNT,
    SELECT_SCREEN_PATCHED_COUNT,
}
SELECT_SCREEN_SIGNATURE_SLOTS: tuple[tuple[int, int], ...] = (
    (0x00, 0x01),  # Ken/Gatchaman
    (0x01, 0x08),  # Jun
    (0x02, 0x02),  # Casshan
    (0x03, 0x03),  # Tekkaman
    (0x0E, 0x1D),  # Zero
    (0x0F, 0x1E),  # Frank
    (0x19, 0x0D),  # Chun-Li
    (0x1A, 0x0C),  # Ryu
)


def _select_screen_status() -> dict[str, Any]:
    counts: dict[str, int | None] = {
        f"0x{addr:08X}": _safe_read_u32be(addr) for addr, _label in ROSTER_COUNT_ADDRS
    }
    slots: dict[str, int | None] = {
        f"0x{_roster_addr_for_slot(slot):08X}": _safe_read_u32be(_roster_addr_for_slot(slot))
        for slot, _expected in SELECT_SCREEN_SIGNATURE_SLOTS
    }

    count_ok = bool(counts) and all(v in SELECT_SCREEN_COUNT_VALUES for v in counts.values())
    slot_ok = True
    for slot, expected in SELECT_SCREEN_SIGNATURE_SLOTS:
        actual = slots.get(f"0x{_roster_addr_for_slot(slot):08X}")
        if actual != expected:
            slot_ok = False
            break

    active = bool(count_ok and slot_ok)

    clone_rows_present = bool(
        active
        and all(_safe_read_u32be(addr) == SELECT_SCREEN_PATCHED_COUNT for addr, _label in ROSTER_COUNT_ADDRS)
        and _safe_read_u32be(_roster_addr_for_slot(0x1B)) == 0x17
        and _safe_read_u32be(_roster_addr_for_slot(0x1C)) == 0x18
        and _safe_read_u32be(_roster_addr_for_slot(0x1D)) == 0x19
    )

    # Important: the first guarded build only checked the logical roster rows.
    # If the shell/count were already present, it skipped the visual/profile
    # append and the three Yami slots kept borrowing the same selected face
    # (usually three Ryus or three Kens). Treat the patch as incomplete unless
    # the three donor profile rows are also present.
    expected_visual = _pack_u32s(VISUAL_TABLE_THREE_DONOR_APPEND_INDEX)
    expected_chars = _pack_u32s(VISUAL_TABLE_YAMI_CHAR_APPEND)
    actual_visual = _safe_read(VISUAL_TABLE_APPEND_INDEX_ADDR, len(expected_visual)) or b""
    actual_chars = _safe_read(VISUAL_TABLE_APPEND_CHAR_ADDR, len(expected_chars)) or b""
    visual_rows_present = bool(
        active
        and actual_visual == expected_visual
        and actual_chars == expected_chars
    )

    patch_present = bool(clone_rows_present and visual_rows_present)
    return {
        "active": active,
        "patch_present": patch_present,
        "clone_rows_present": clone_rows_present,
        "visual_rows_present": visual_rows_present,
        "count_ok": count_ok,
        "slot_ok": slot_ok,
        "counts": {k: _hex(v) for k, v in counts.items()},
        "slots": {k: _hex(v) for k, v in slots.items()},
        "visual_append_addr": f"0x{VISUAL_TABLE_APPEND_INDEX_ADDR:08X}",
        "visual_append_expected": [f"0x{x:08X}" for x in VISUAL_TABLE_THREE_DONOR_APPEND_INDEX],
        "visual_append_actual": _read_u32_list(VISUAL_TABLE_APPEND_INDEX_ADDR, len(VISUAL_TABLE_THREE_DONOR_APPEND_INDEX)),
        "visual_char_addr": f"0x{VISUAL_TABLE_APPEND_CHAR_ADDR:08X}",
        "visual_char_expected": [f"0x{x:08X}" for x in VISUAL_TABLE_YAMI_CHAR_APPEND],
        "visual_char_actual": _read_u32_list(VISUAL_TABLE_APPEND_CHAR_ADDR, len(VISUAL_TABLE_YAMI_CHAR_APPEND)),
    }


def _update_extra_guard_state(status: dict[str, Any] | None = None) -> dict[str, Any]:
    if status is None:
        status = _select_screen_status()
    with _LOCK:
        _ROSTER_STATE["extra_characters_select_active"] = bool(status.get("active"))
        _ROSTER_STATE["extra_characters_patch_present"] = bool(status.get("patch_present"))
        if status.get("active"):
            _ROSTER_STATE["extra_characters_guard"] = "character select detected"
        else:
            _ROSTER_STATE["extra_characters_guard"] = "waiting for character select"
    return status


def _tick_extra_characters_request() -> None:
    with _LOCK:
        requested = bool(_ROSTER_STATE.get("extra_characters_requested"))
    status = _update_extra_guard_state()
    active = bool(status.get("active"))
    present = bool(status.get("patch_present"))

    if not requested:
        # OFF means do not apply. If the user toggles off outside character
        # select, do not try to restore stale select-screen addresses.
        with _LOCK:
            if not active:
                _ROSTER_STATE["extra_characters_enabled"] = False
                _ROSTER_STATE["extra_characters_mode"] = ""
        return

    if not active:
        with _LOCK:
            _ROSTER_STATE["extra_characters_enabled"] = False
            _ROSTER_STATE["last_action"] = "Extra characters armed; waiting for character select screen"
            _ROSTER_STATE["last_error"] = ""
        return

    if present:
        # Keep the three donor profile rows warm while character select is open.
        # The game can rebuild parts of this table when entering/leaving character
        # select, and the OFF/ON guard used to consider the patch complete as soon
        # as the Yami roster rows existed. This refresh is small and only runs
        # after the select-screen signature passes.
        with _LOCK:
            _ROSTER_STATE["extra_characters_enabled"] = True
            _ROSTER_STATE["extra_characters_mode"] = "Yami 1/2/3 with Frank, Blade, Yatterman-2 profile rows"
            _ROSTER_STATE["last_error"] = ""
        return

    wrote, failed = _install_extra_characters_on()
    with _LOCK:
        _ROSTER_STATE["patches"] = int(_ROSTER_STATE.get("patches", 0) or 0) + int(wrote)
        _ROSTER_STATE["failed"] = int(_ROSTER_STATE.get("failed", 0) or 0) + int(failed)
        _ROSTER_STATE["last_action"] = f"Extra characters auto-applied on character select wrote={wrote} failed={failed}"
        _ROSTER_STATE["last_error"] = "" if failed == 0 else f"Extra characters auto-apply failed writes={failed}"
    _update_extra_guard_state()


def _tick_solo_team_request() -> None:
    with _LOCK:
        requested = bool(_ROSTER_STATE.get("solo_team_requested"))
        extra_requested = bool(_ROSTER_STATE.get("extra_characters_requested"))
    status = _update_extra_guard_state()
    active = bool(status.get("active"))
    visual_present = bool(status.get("visual_rows_present"))

    if requested:
        if not active:
            with _LOCK:
                _ROSTER_STATE["solo_team_enabled"] = False
                _ROSTER_STATE["solo_team_guard"] = "armed; waiting for character select"
            return
        if not visual_present:
            wrote, failed = _install_extra_profile_rows_only("Solo-team profile rows")
            with _LOCK:
                _ROSTER_STATE["patches"] = int(_ROSTER_STATE.get("patches", 0) or 0) + int(wrote)
                _ROSTER_STATE["failed"] = int(_ROSTER_STATE.get("failed", 0) or 0) + int(failed)
                _ROSTER_STATE["last_action"] = f"Solo team auto-applied profile rows wrote={wrote} failed={failed}"
                _ROSTER_STATE["last_error"] = "" if failed == 0 else f"Solo team apply failed writes={failed}"
            status = _update_extra_guard_state()
            visual_present = bool(status.get("visual_rows_present"))
        with _LOCK:
            _ROSTER_STATE["solo_team_enabled"] = bool(visual_present)
            _ROSTER_STATE["solo_team_mode"] = "Profile-row solo-team helper" if visual_present else ""
            _ROSTER_STATE["solo_team_guard"] = "character select detected"
        return

    # Solo OFF should not tear out the shared profile rows while Extra chars is ON,
    # because Extra chars intentionally uses those same rows for the three borrowed
    # profile pictures.
    if active and visual_present and not extra_requested:
        wrote, failed = _restore_extra_profile_rows_only()
        with _LOCK:
            _ROSTER_STATE["patches"] = int(_ROSTER_STATE.get("patches", 0) or 0) + int(wrote)
            _ROSTER_STATE["failed"] = int(_ROSTER_STATE.get("failed", 0) or 0) + int(failed)
            _ROSTER_STATE["last_action"] = f"Solo team OFF restored profile rows wrote={wrote} failed={failed}"
            _ROSTER_STATE["last_error"] = "" if failed == 0 else f"Solo team restore failed writes={failed}"
    with _LOCK:
        _ROSTER_STATE["solo_team_enabled"] = False
        _ROSTER_STATE["solo_team_mode"] = ""
        _ROSTER_STATE["solo_team_guard"] = "off"


# Owned scratch visual-bank experiment.
# This is the user's "stop hijacking strings, make our own stuff and point to it" path.
# We allocate a private MEM2 bank, clone the live select visual text bank into it,
# rewrite the clone to Frank, then redirect the owner pointer pairs to the clone.
OWNED_VISUAL_BANK_ADDR = 0x93600000
OWNED_VISUAL_BANK_SOURCE_ADDR = 0x90818400
OWNED_VISUAL_BANK_SIZE = 0x180
OWNED_VISUAL_OWNER_PTRS: tuple[tuple[int, int, str], ...] = (
    (0x90844A18, OWNED_VISUAL_BANK_ADDR + 0x68, "owner A material prefix -> owned bank"),
    (0x90844A1C, OWNED_VISUAL_BANK_ADDR + 0x70, "owner A tag -> owned bank"),
    (0x90844AD8, OWNED_VISUAL_BANK_ADDR + 0x68, "owner B material prefix -> owned bank"),
    (0x90844ADC, OWNED_VISUAL_BANK_ADDR + 0x70, "owner B tag -> owned bank"),
)


def _put_cstr(buf: bytearray, off: int, size: int, text: bytes) -> None:
    start = int(off)
    end = start + int(size)
    payload = bytes(text)[: max(0, int(size) - 1)] + b"\x00"
    payload = payload[: int(size)].ljust(int(size), b"\x00")
    buf[start:end] = payload


def queue_yami_owned_frank_bank() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_owned_frank_bank"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "owned scratch Frank visual bank queued"
    return {"ok": True, "queued": True, "label": "Owned Frank bank"}


def queue_yami_owned_bank_snapshot() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_owned_bank_snapshot"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "owned bank snapshot queued"
    return {"ok": True, "queued": True, "label": "Owned bank snapshot"}


def _install_yami_owned_frank_bank() -> tuple[int, int]:
    wrote = 0
    failed = 0

    source = _safe_read(OWNED_VISUAL_BANK_SOURCE_ADDR, OWNED_VISUAL_BANK_SIZE)
    if not source or len(source) < OWNED_VISUAL_BANK_SIZE:
        return 0, 1

    bank = bytearray(source[:OWNED_VISUAL_BANK_SIZE])

    # Patch both possible representations inside the cloned bank:
    # full material string at +0x60, prefix/tag pair at +0x68/+0x70,
    # select/name/icon fields after the tag. These sizes match the live bank.
    _put_cstr(bank, 0x38, 0x0C, b"select_fra")
    _put_cstr(bank, 0x4C, 0x0C, b"name_fra")
    _put_cstr(bank, 0x60, 0x08, b"mof_fra")
    _put_cstr(bank, 0x68, 0x08, b"mof_")
    _put_cstr(bank, 0x70, 0x08, b"fra")
    _put_cstr(bank, 0x78, 0x0C, b"select_fra")
    _put_cstr(bank, 0x84, 0x08, b"name_fra")
    _put_cstr(bank, 0x8C, 0x10, b"icon_fra")
    _put_cstr(bank, 0x9C, 0x10, b"icon_fra")
    _put_cstr(bank, 0xAC, 0x08, b"fra")

    if _write_bytes_saved(OWNED_VISUAL_BANK_ADDR, bytes(bank), expected=None, size=OWNED_VISUAL_BANK_SIZE):
        wrote += 1
    else:
        failed += 1

    for ptr_addr, target, _label in OWNED_VISUAL_OWNER_PTRS:
        if _write_saved(ptr_addr, target):
            wrote += 1
        else:
            failed += 1

    with _LOCK:
        _ROSTER_STATE["owned_bank_installed"] = failed == 0
        _ROSTER_STATE["owned_bank_addr"] = f"0x{OWNED_VISUAL_BANK_ADDR:08X}"
    return wrote, failed


def _capture_yami_owned_bank_snapshot() -> dict[str, Any]:
    def _ascii(addr: int, size: int) -> str:
        data = _safe_read(addr, size)
        if not data:
            return ""
        return "".join(chr(b) if 32 <= b < 127 else "." for b in data)

    snap = {
        "owned_bank_addr": f"0x{OWNED_VISUAL_BANK_ADDR:08X}",
        "owned_bank_ascii": _ascii(OWNED_VISUAL_BANK_ADDR, 0xC0),
        "source_bank_ascii": _ascii(OWNED_VISUAL_BANK_SOURCE_ADDR, 0xC0),
        "owner_ptrs": {
            "0x90844A18": _hex(_safe_read_u32be(0x90844A18)),
            "0x90844A1C": _hex(_safe_read_u32be(0x90844A1C)),
            "0x90844AD8": _hex(_safe_read_u32be(0x90844AD8)),
            "0x90844ADC": _hex(_safe_read_u32be(0x90844ADC)),
        },
    }
    with _LOCK:
        _ROSTER_STATE["owned_bank_snapshot"] = snap
        _ROSTER_STATE["last_action"] = "owned bank snapshot captured"
        _ROSTER_STATE["last_error"] = ""
    return snap




# Static visual-grid table probe.
# The live roster table now has slots 0x1B..0x1D, but the visual wheel still
# appears to be backed by older static grid lists around 0x8036E7xx. The two
# relevant pairs are:
#   0x8036E774: Capcom-side visual/material indices, zero-based char index
#   0x8036E7EC: Capcom-side character IDs
# These lists end with FFFFFFFF and are packed directly next to other tables,
# so extending them in place is intentionally marked as a destructive lab probe.
# Restore puts the original bytes back.
VISUAL_TABLE_INDEX_ADDR = 0x8036E774
VISUAL_TABLE_CHAR_ADDR = 0x8036E7EC
VISUAL_TABLE_WORD_COUNT = 0x18  # read enough to see the terminator and neighbors
VISUAL_TABLE_APPEND_INDEX_ADDR = 0x8036E7AC  # current FF terminator of index list
VISUAL_TABLE_APPEND_CHAR_ADDR = 0x8036E824   # current FF terminator of char-id list
VISUAL_TABLE_PATCH_SIZE = 0x10

# Frank visual index is char_id(0x1E)-1 = 0x1D in the zero-based visual list.
# Keep Yami as actual char IDs 0x17/0x18/0x19 in the char-id list.
VISUAL_TABLE_FRANK_FACE_APPEND_INDEX = (0x1D, 0x1D, 0x1D, 0xFFFFFFFF)
VISUAL_TABLE_YAMI_CHAR_APPEND = (0x17, 0x18, 0x19, 0xFFFFFFFF)

# Native Yami visual-index append, useful if the engine actually has hidden tk1/tk2/tk3 panes.
VISUAL_TABLE_NATIVE_YAMI_APPEND_INDEX = (0x16, 0x17, 0x18, 0xFFFFFFFF)

# Automated extra-character visual/profile rows. These keep the real Yami
# character IDs in the logical roster table, but borrow three obvious, different
# profile/card visual indices so the added slots stop sharing one profile.
# The visual index list is zero-based against character ID, so:
#   Frank West      char 0x1E -> visual index 0x1D
#   Tekkaman Blade  char 0x1A -> visual index 0x19
#   Yatterman-2     char 0x1C -> visual index 0x1B
VISUAL_TABLE_THREE_DONOR_APPEND_INDEX = (0x1D, 0x19, 0x1B, 0xFFFFFFFF)
VISUAL_TABLE_THREE_DONOR_LABEL = "Frank / Blade / Yatterman-2 visual indices + Yami char IDs"


def queue_yami_visual_table_snapshot() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_visual_table_snapshot"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "visual table snapshot queued"
    return {"ok": True, "queued": True, "label": "Visual table snapshot"}


def queue_yami_visual_table_frank_append() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_visual_table_frank_append"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "Frank visual table append queued"
    return {"ok": True, "queued": True, "label": "Frank visual table append"}


def queue_yami_visual_table_native_append() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_visual_table_native_append"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "native Yami visual table append queued"
    return {"ok": True, "queued": True, "label": "Native Yami visual table append"}


def queue_yami_visual_table_three_donor_append() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_visual_table_three_donor_append"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "three-donor visual table append queued"
    return {"ok": True, "queued": True, "label": "Three-donor visual table append"}


def queue_extra_characters_on() -> dict[str, Any]:
    status = _update_extra_guard_state()
    with _LOCK:
        _ROSTER_STATE["extra_characters_requested"] = True
        _ROSTER_STATE["last_action"] = (
            "Extra characters ON armed; will apply on character select"
            if not status.get("active")
            else "Extra characters ON armed; applying on tick"
        )
        _ROSTER_STATE["last_error"] = ""
    return {"ok": True, "queued": False, "requested": True, "select_active": bool(status.get("active"))}


def queue_extra_characters_off() -> dict[str, Any]:
    status = _update_extra_guard_state()
    with _LOCK:
        _ROSTER_STATE["extra_characters_requested"] = False
        _ROSTER_STATE["last_error"] = ""
        if not status.get("active"):
            _ROSTER_STATE["extra_characters_enabled"] = False
            _ROSTER_STATE["extra_characters_mode"] = ""
            _ROSTER_STATE["last_action"] = "Extra characters OFF; no character-select write needed"
            return {"ok": True, "queued": False, "requested": False, "select_active": False}
        _ROSTER_QUEUE.append({"op": "extra_chars_off"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "Extra characters OFF queued for character select restore"
    return {"ok": True, "queued": True, "requested": False, "select_active": True}


def toggle_extra_characters() -> dict[str, Any]:
    with _LOCK:
        enabled = bool(_ROSTER_STATE.get("extra_characters_enabled") or _ROSTER_STATE.get("extra_characters_requested"))
    if enabled:
        return queue_extra_characters_off()
    return queue_extra_characters_on()


def queue_solo_team_on() -> dict[str, Any]:
    status = _update_extra_guard_state()
    with _LOCK:
        _ROSTER_STATE["solo_team_requested"] = True
        _ROSTER_STATE["solo_team_enabled"] = bool(status.get("active") and status.get("visual_rows_present"))
        _ROSTER_STATE["solo_team_mode"] = "Profile-row solo-team helper"
        if status.get("active"):
            _ROSTER_STATE["solo_team_guard"] = "character select detected"
            _ROSTER_QUEUE.append({"op": "solo_team_on"})
        else:
            _ROSTER_STATE["solo_team_guard"] = "armed; waiting for character select"
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "Solo team ON queued" if status.get("active") else "Solo team ON armed; waiting for character select"
        _ROSTER_STATE["last_error"] = ""
    return {"ok": True, "requested": True, "active": bool(status.get("active")), "label": "Solo team ON"}


def queue_solo_team_off() -> dict[str, Any]:
    status = _update_extra_guard_state()
    with _LOCK:
        _ROSTER_STATE["solo_team_requested"] = False
        if status.get("active"):
            _ROSTER_QUEUE.append({"op": "solo_team_off"})
        else:
            _ROSTER_STATE["solo_team_enabled"] = False
            _ROSTER_STATE["solo_team_mode"] = ""
            _ROSTER_STATE["solo_team_guard"] = "off; not on character select"
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "Solo team OFF queued" if status.get("active") else "Solo team OFF deferred; not on character select"
        _ROSTER_STATE["last_error"] = ""
    return {"ok": True, "requested": False, "active": bool(status.get("active")), "label": "Solo team OFF"}


def toggle_solo_team() -> dict[str, Any]:
    with _LOCK:
        enabled = bool(_ROSTER_STATE.get("solo_team_enabled") or _ROSTER_STATE.get("solo_team_requested"))
    if enabled:
        return queue_solo_team_off()
    return queue_solo_team_on()


def _read_u32_list(addr: int, count: int) -> list[str]:
    out: list[str] = []
    for i in range(int(count)):
        v = _safe_read_u32be(addr + i * 4)
        out.append("?" if v is None else f"0x{v:08X}")
    return out


def _visual_table_snapshot() -> dict[str, Any]:
    snap = {
        "index_table_addr": f"0x{VISUAL_TABLE_INDEX_ADDR:08X}",
        "char_table_addr": f"0x{VISUAL_TABLE_CHAR_ADDR:08X}",
        "index_table": _read_u32_list(VISUAL_TABLE_INDEX_ADDR, VISUAL_TABLE_WORD_COUNT),
        "char_table": _read_u32_list(VISUAL_TABLE_CHAR_ADDR, VISUAL_TABLE_WORD_COUNT),
        "append_index_addr": f"0x{VISUAL_TABLE_APPEND_INDEX_ADDR:08X}",
        "append_char_addr": f"0x{VISUAL_TABLE_APPEND_CHAR_ADDR:08X}",
    }
    with _LOCK:
        _ROSTER_STATE["visual_table_snapshot"] = snap
        _ROSTER_STATE["last_action"] = "visual table snapshot captured"
        _ROSTER_STATE["last_error"] = ""
    return snap


def _pack_u32s(values: tuple[int, ...]) -> bytes:
    return b"".join(int(v & 0xFFFFFFFF).to_bytes(4, "big") for v in values)


VISUAL_TABLE_ORIGINAL_APPEND_INDEX = (0xFFFFFFFF, 0x00000001, 0x00000008, 0x00000002)
VISUAL_TABLE_ORIGINAL_APPEND_CHAR = (0xFFFFFFFF, 0x00000000, 0x00000001, 0x00000005)


def _install_extra_profile_rows_only(label: str = VISUAL_TABLE_THREE_DONOR_LABEL) -> tuple[int, int]:
    """Install only the profile/card table rows.

    This is the part that produced the accidental one-character team behavior
    after leaving and re-entering character select. Keep it as an independent
    guarded feature instead of tying it only to the Yami roster extension.
    """
    wrote = 0
    failed = 0
    idx_payload = _pack_u32s(VISUAL_TABLE_THREE_DONOR_APPEND_INDEX)
    char_payload = _pack_u32s(VISUAL_TABLE_YAMI_CHAR_APPEND)
    if _write_bytes_saved(VISUAL_TABLE_APPEND_INDEX_ADDR, idx_payload, expected=None, size=len(idx_payload)):
        wrote += 1
    else:
        failed += 1
    if _write_bytes_saved(VISUAL_TABLE_APPEND_CHAR_ADDR, char_payload, expected=None, size=len(char_payload)):
        wrote += 1
    else:
        failed += 1
    snap = _visual_table_snapshot()
    with _LOCK:
        _ROSTER_STATE["visual_table_patch_installed"] = failed == 0
        _ROSTER_STATE["visual_table_patch_mode"] = label if failed == 0 else ""
        _ROSTER_STATE["visual_table_snapshot"] = snap
        _ROSTER_STATE["restore_available"] = bool(_ROSTER_ORIGINALS or _ROSTER_BYTE_ORIGINALS)
        _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
    return wrote, failed


def _restore_extra_profile_rows_only() -> tuple[int, int]:
    wrote = 0
    failed = 0
    idx_payload = _pack_u32s(VISUAL_TABLE_ORIGINAL_APPEND_INDEX)
    char_payload = _pack_u32s(VISUAL_TABLE_ORIGINAL_APPEND_CHAR)
    if _write_bytes_saved(VISUAL_TABLE_APPEND_INDEX_ADDR, idx_payload, expected=None, size=len(idx_payload)):
        wrote += 1
    else:
        failed += 1
    if _write_bytes_saved(VISUAL_TABLE_APPEND_CHAR_ADDR, char_payload, expected=None, size=len(char_payload)):
        wrote += 1
    else:
        failed += 1
    with _LOCK:
        if failed == 0:
            _ROSTER_STATE["visual_table_patch_installed"] = False
            _ROSTER_STATE["visual_table_patch_mode"] = ""
        _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
    return wrote, failed


def _restore_extra_roster_rows_only() -> tuple[int, int]:
    wrote = 0
    failed = 0
    for addr, _label in ROSTER_COUNT_ADDRS:
        if _write_saved(addr, SELECT_SCREEN_STOCK_COUNT):
            wrote += 1
        else:
            failed += 1
    for slot, _cid, _name in YAMI_CLONE_SLOTS:
        if _write_saved(_roster_addr_for_slot(slot), 0):
            wrote += 1
        else:
            failed += 1
    with _LOCK:
        if failed == 0:
            _ROSTER_STATE["clone_table_installed"] = False
            _ROSTER_STATE["clone_count_installed"] = False
    return wrote, failed


def _install_yami_visual_table_append(index_values: tuple[int, ...], label: str) -> tuple[int, int]:
    wrote = 0
    failed = 0
    # Keep logical Yami rows/count installed too, otherwise the visual extension has nothing to match.
    w, f = _install_yami_clone_table()
    wrote += w
    failed += f
    w, f = _install_yami_clone_count()
    wrote += w
    failed += f

    if tuple(index_values) == tuple(VISUAL_TABLE_THREE_DONOR_APPEND_INDEX):
        w, f = _install_extra_profile_rows_only(label)
        wrote += w
        failed += f
    else:
        idx_payload = _pack_u32s(index_values)
        char_payload = _pack_u32s(VISUAL_TABLE_YAMI_CHAR_APPEND)
        if _write_bytes_saved(VISUAL_TABLE_APPEND_INDEX_ADDR, idx_payload, expected=None, size=len(idx_payload)):
            wrote += 1
        else:
            failed += 1
        if _write_bytes_saved(VISUAL_TABLE_APPEND_CHAR_ADDR, char_payload, expected=None, size=len(char_payload)):
            wrote += 1
        else:
            failed += 1
        snap = _visual_table_snapshot()
        with _LOCK:
            _ROSTER_STATE["visual_table_patch_installed"] = failed == 0
            _ROSTER_STATE["visual_table_patch_mode"] = label
            _ROSTER_STATE["visual_table_snapshot"] = snap
            _ROSTER_STATE["restore_available"] = bool(_ROSTER_ORIGINALS or _ROSTER_BYTE_ORIGINALS)
            _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
    return wrote, failed


def _install_extra_characters_on() -> tuple[int, int]:
    wrote = 0
    failed = 0

    # Shell attempt: install the logical Yami slots and selector count bump.
    # Keep the old silhouette alias because it is the bit that made the hidden
    # shell show up during earlier testing.
    w, f = _install_yami_clone_table()
    wrote += w
    failed += f
    w, f = _install_yami_clone_count()
    wrote += w
    failed += f
    w, f = _install_yami_visual_alias("zero")
    wrote += w
    failed += f

    # Borrowed labels/icons are harmless fallback labels. The proven profile/card
    # layer is handled below by the three-donor visual table append.
    w, f = _install_yami_borrowed_icons()
    wrote += w
    failed += f

    w, f = _install_yami_visual_table_append(
        VISUAL_TABLE_THREE_DONOR_APPEND_INDEX,
        VISUAL_TABLE_THREE_DONOR_LABEL,
    )
    wrote += w
    failed += f

    with _LOCK:
        _ROSTER_STATE["extra_characters_enabled"] = failed == 0
        _ROSTER_STATE["extra_characters_requested"] = failed == 0
        _ROSTER_STATE["extra_characters_mode"] = "Yami 1 -> Frank, Yami 2 -> Blade, Yami 3 -> Yatterman-2 profile rows"
        _ROSTER_STATE["last_action"] = f"Extra characters ON wrote={wrote} failed={failed}; profile rows=Frank/Blade/Yatterman-2"
        _ROSTER_STATE["last_error"] = "" if failed == 0 else f"Extra characters ON failed writes={failed}"
    return wrote, failed

def _hex(value: int | None) -> str:
    if value is None:
        return "?"
    return f"0x{int(value) & 0xFFFFFFFF:08X}"


def _fmt_hex(value: int, width: int = 2) -> str:
    return f"0x{int(value) & ((1 << (width * 4)) - 1):0{width}X}"


def _char_name(char_id: int | None) -> str:
    if char_id is None:
        return "unknown"
    return CHAR_ID_TO_NAME.get(int(char_id) & 0xFFFFFFFF, f"unknown {_fmt_hex(int(char_id) & 0xFFFFFFFF, 2)}")


def _char_label(char_id: int | None) -> str:
    if char_id is None:
        return "unknown"
    return f"{_char_name(char_id)} (ID {_fmt_hex(int(char_id) & 0xFF, 2)})"


def _slot_label(slot: int, char_id: int | None = None) -> str:
    slot_i = int(slot) & 0xFF
    default_id = _SLOT_TO_ID.get(slot_i)
    cid = int(default_id if char_id is None else char_id) & 0xFFFFFFFF
    default_name = _SLOT_TO_DEFAULT_NAME.get(slot_i, f"slot {_fmt_hex(slot_i, 2)}")
    return f"{default_name} slot {_fmt_hex(slot_i, 2)} (ID {_fmt_hex(cid & 0xFF, 2)})"


def _parse_first_int(text: str, default: int = 0) -> int:
    m = _INT_RE.search(str(text))
    if not m:
        return int(default) & 0xFFFFFFFF
    token = m.group(0)
    return int(token, 16 if token.lower().startswith("0x") else 10) & 0xFFFFFFFF


def _parse_int_value(value: Any, default: int = 0) -> int:
    try:
        text = str(value).strip().lower()
        if ":" in text:
            text = text.split(":", 1)[0].strip()
        return _parse_first_int(text, default)
    except Exception:
        return int(default) & 0xFFFFFFFF


def _parse_slot_value(value: Any, default: int = 0x1A) -> int:
    text = str(value).strip()
    lower = text.lower()

    # Preferred UI format contains "slot 0xNN".
    m = re.search(r"\bslot\s*(0x[0-9a-fA-F]+|\d+)\b", text, flags=re.IGNORECASE)
    if m:
        token = m.group(1)
        return int(token, 16 if token.lower().startswith("0x") else 10) & 0xFF

    # Support old format: "0x1A : Ryu wheel slot".
    m = re.match(r"\s*(0x[0-9a-fA-F]+|\d+)\b", text)
    if m:
        token = m.group(1)
        return int(token, 16 if token.lower().startswith("0x") else 10) & 0xFF

    # Support plain character names.
    for name, slot in _NAME_TO_SLOT.items():
        if name in lower:
            return int(slot) & 0xFF

    return int(default) & 0xFF


def _parse_char_id_value(value: Any, default: int = 0x0D) -> int:
    text = str(value).strip()
    lower = text.lower()

    # Preferred UI format contains "ID 0xNN".
    m = re.search(r"\bID\s*(0x[0-9a-fA-F]+|\d+)\b", text, flags=re.IGNORECASE)
    if m:
        token = m.group(1)
        return int(token, 16 if token.lower().startswith("0x") else 10) & 0xFFFFFFFF

    # Support old format: "0x0D : Chun-Li".
    m = re.match(r"\s*(0x[0-9a-fA-F]+|\d+)\b", text)
    if m:
        token = m.group(1)
        return int(token, 16 if token.lower().startswith("0x") else 10) & 0xFFFFFFFF

    # Support plain character names.
    for name, cid in _NAME_TO_ID.items():
        if name in lower:
            return int(cid) & 0xFFFFFFFF

    return int(default) & 0xFFFFFFFF


def _roster_addr_for_slot(slot_index: int) -> int:
    return int(ROSTER_TABLE_BASE + (int(slot_index) & 0xFF) * 4)


def _safe_read(addr: int, size: int) -> bytes:
    if rbytes is None:
        return b""
    try:
        data = rbytes(int(addr), int(size))
    except Exception:
        return b""
    if not data:
        return b""
    return bytes(data)


def _safe_read_u32be(addr: int) -> int | None:
    data = _safe_read(int(addr), 4)
    if not data or len(data) < 4:
        return None
    try:
        return int.from_bytes(data[:4], "big")
    except Exception:
        return None


def _safe_write_u32be(addr: int, value: int) -> bool:
    if wd32 is None:
        return False
    try:
        wd32(int(addr), int(value) & 0xFFFFFFFF)
        return True
    except Exception as e:
        with _LOCK:
            _ROSTER_STATE["last_error"] = repr(e)
        return False


def _safe_write_bytes(addr: int, data: bytes) -> bool:
    if wbytes is None:
        return False
    try:
        wbytes(int(addr), bytes(data))
        return True
    except Exception as e:
        with _LOCK:
            _ROSTER_STATE["last_error"] = repr(e)
        return False


def _read_roster_selector_snapshot() -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    for addr, label in ROSTER_SELECTOR_ADDRS:
        value = _safe_read_u32be(addr)
        item = {
            "addr": f"0x{addr:08X}",
            "label": label,
            "value": (f"0x{value:08X}" if value is not None else ""),
            "display": "",
        }
        if value is not None and "char id" in label:
            item["display"] = _char_label(value)
        elif value is not None and "selected" in label:
            item["display"] = _char_label(value)
        elif value is not None and "cursor index" in label:
            slot = int(value) & 0xFF
            cid = _SLOT_TO_ID.get(slot)
            item["display"] = _slot_label(slot, cid) if cid is not None else f"slot {_fmt_hex(slot, 2)}"
        fields.append(item)

    counts: list[dict[str, Any]] = []
    for addr, label in ROSTER_COUNT_ADDRS:
        value = _safe_read_u32be(addr)
        counts.append({
            "addr": f"0x{addr:08X}",
            "label": label,
            "value": (f"0x{int(value):08X}" if value is not None else ""),
            "is_clone_count": bool(value == YAMI_CLONE_COUNT),
        })

    table: list[dict[str, Any]] = []
    for slot, default_cid, default_name in ROSTER_SLOT_TABLE:
        addr = _roster_addr_for_slot(slot)
        value = _safe_read_u32be(addr)
        table.append({
            "slot": f"0x{slot:02X}",
            "slot_i": slot,
            "addr": f"0x{addr:08X}",
            "default_name": default_name,
            "default_char_id": f"0x{default_cid:02X}",
            "default_label": _char_label(default_cid),
            "char_id": (f"0x{value & 0xFF:02X}" if value is not None else ""),
            "char_label": (_char_label(value) if value is not None else ""),
            "patched": bool(value is not None and int(value) != int(default_cid)),
        })

    hover_idx = _safe_read_u32be(0x809BCEA0)
    hover_slot = int(hover_idx) if hover_idx is not None else None
    hover_slot_addr = _roster_addr_for_slot(hover_slot) if hover_slot is not None and 0 <= hover_slot <= 0x40 else 0
    hover_slot_value = _safe_read_u32be(hover_slot_addr) if hover_slot_addr else None
    hover_default_id = _SLOT_TO_ID.get(hover_slot or -1)

    return {
        "fields": fields,
        "counts": counts,
        "table": table,
        "hover_index": (f"0x{int(hover_idx):02X}" if hover_idx is not None else ""),
        "hover_slot_addr": (f"0x{hover_slot_addr:08X}" if hover_slot_addr else ""),
        "hover_slot_default": (_char_label(hover_default_id) if hover_default_id is not None else ""),
        "hover_slot_value": (f"0x{int(hover_slot_value) & 0xFF:02X}" if hover_slot_value is not None else ""),
        "hover_slot_label": (_char_label(hover_slot_value) if hover_slot_value is not None else ""),
        "roster_base": f"0x{ROSTER_TABLE_BASE:08X}",
    }


def get_roster_slot_choices() -> list[str]:
    return [f"{name} slot 0x{slot:02X} (ID 0x{cid:02X})" for slot, cid, name in ROSTER_SLOT_TABLE]


def get_roster_char_choices() -> list[str]:
    # Visible wheel characters first, in roster-table order. Then append any
    # known non-wheel / hidden IDs so they can be swiped into existing slots.
    seen: set[int] = set()
    out: list[str] = []
    for _slot, cid, _name in ROSTER_SLOT_TABLE:
        if cid in seen:
            continue
        seen.add(cid)
        out.append(_char_label(cid))
    for cid in sorted(CHAR_ID_TO_NAME):
        if cid in seen:
            continue
        seen.add(cid)
        out.append(_char_label(cid))
    return out


def queue_roster_snapshot() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "snapshot"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "selector snapshot queued"
    return {"ok": True, "queued": True}


def queue_roster_patch_slot(slot_index: Any = 0x1A, target_char_id: Any = 0x0D) -> dict[str, Any]:
    slot = _parse_slot_value(slot_index, 0x1A) & 0xFF
    target = _parse_char_id_value(target_char_id, 0x0D) & 0xFFFFFFFF
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "patch_slot", "slot": slot, "target": target})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = f"patch queued {_slot_label(slot)} -> {_char_label(target)}"
    return {"ok": True, "queued": True, "slot": f"0x{slot:02X}", "slot_label": _slot_label(slot), "target": f"0x{target:08X}", "target_label": _char_label(target)}


def queue_roster_patch_current_hover(target_char_id: Any = 0x0D) -> dict[str, Any]:
    target = _parse_char_id_value(target_char_id, 0x0D) & 0xFFFFFFFF
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "patch_current", "target": target})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = f"patch current hover queued -> {_char_label(target)}"
    return {"ok": True, "queued": True, "target": f"0x{target:08X}", "target_label": _char_label(target)}


def queue_yami_clone_table() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_clone_table"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "Yami clone table install queued"
    return {"ok": True, "queued": True}


def queue_yami_clone_count() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_clone_count"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "Yami clone count bump queued"
    return {"ok": True, "queued": True}


def queue_yami_clone_install_all() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_clone_all"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "Yami clone table + count queued"
    return {"ok": True, "queued": True}


def queue_yami_visual_alias(mode: str = "zero") -> dict[str, Any]:
    mode_key = str(mode or "zero").strip().lower()
    if mode_key not in VISUAL_ALIAS_PRESETS:
        mode_key = "zero"
    label = VISUAL_ALIAS_PRESETS[mode_key][0]
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_visual_alias", "mode": mode_key})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = f"Yami visual alias queued: {label}"
    return {"ok": True, "queued": True, "mode": mode_key, "label": label}





def queue_yami_far_donor_probe(donor: str = "fra") -> dict[str, Any]:
    # Disabled after runtime test: writing the active 0x90818460/0x90818470
    # material/tag bank can freeze cursor movement. That bank is a live output
    # buffer/owner, not a safe icon source. Keep this function so old UI calls
    # do not crash, but do not queue any writes.
    donor_key = str(donor or "fra").strip().lower()
    if donor_key not in FAR_DONOR_TARGETS:
        donor_key = "fra"
    label = str(FAR_DONOR_TARGETS[donor_key]["label"])
    with _LOCK:
        _ROSTER_STATE["last_action"] = f"disabled unsafe far-donor probe: {label}"
        _ROSTER_STATE["last_error"] = "Far donor hard probe disabled: 0x90818460/0x90818470 froze cursor."
    return {"ok": False, "queued": False, "disabled": True, "donor": donor_key, "label": label}

def queue_yami_face_resource_probe(donor: str = "gac") -> dict[str, Any]:
    donor_key = str(donor or "gac").strip().lower()
    if donor_key not in FACE_RESOURCE_DONORS:
        donor_key = "gac"
    label, _items = FACE_RESOURCE_DONORS[donor_key]
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_face_resource_probe", "donor": donor_key})
        _ROSTER_STATE["queued"] += 1
        _ROSTER_STATE["last_action"] = f"Yami face resource probe queued: {label}"
    return {"ok": True, "queued": True, "label": label, "donor": donor_key}

def queue_yami_face_block_copy(donor: str = "gac") -> dict[str, Any]:
    donor_key = str(donor or "gac").strip().lower()
    if donor_key not in FACE_BLOCK_COPY_DONORS:
        donor_key = "gac"
    label = FACE_BLOCK_COPY_DONORS[donor_key][0]
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_face_block_copy", "donor": donor_key})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = f"Yami loaded face-block copy queued: {label}"
    return {"ok": True, "queued": True, "label": label, "donor": donor_key}


def queue_yami_pane_probe_snapshot() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_pane_probe_snapshot"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "read-only pane/material probe snapshot queued"
    return {"ok": True, "queued": True, "label": "read-only pane/material snapshot"}



def queue_yami_ken_dupe_soft_frank() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_ken_dupe_soft_frank"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "Ken duplicate soft -> Frank queued"
    return {"ok": True, "queued": True, "label": "Ken duplicate soft -> Frank"}


def queue_yami_ken_dupe_owner_frank(mode: str = "tag") -> dict[str, Any]:
    mode_key = str(mode or "tag").strip().lower()
    if mode_key not in {"tag", "pair"}:
        mode_key = "tag"
    label = "Ken owner tag -> Frank" if mode_key == "tag" else "Ken owner pair -> Frank"
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_ken_dupe_owner_frank", "mode": mode_key})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = f"{label} queued"
    return {"ok": True, "queued": True, "mode": mode_key, "label": label}


def queue_yami_frank_face_lock(mode: str = "tag") -> dict[str, Any]:
    mode_key = str(mode or "tag").strip().lower()
    if mode_key not in {"tag", "pair"}:
        mode_key = "tag"
    label = "Frank face lock" if mode_key == "tag" else "Frank face lock strong pair"
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_frank_face_lock", "mode": mode_key})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = f"{label} queued"
    return {"ok": True, "queued": True, "mode": mode_key, "label": label}

def queue_yami_shell_attempt(mode: str = "zero") -> dict[str, Any]:
    mode_key = str(mode or "zero").strip().lower()
    if mode_key not in VISUAL_ALIAS_PRESETS:
        mode_key = "zero"
    label = VISUAL_ALIAS_PRESETS[mode_key][0]
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_shell_attempt", "mode": mode_key})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = f"Yami shell attempt queued: {label}"
    return {"ok": True, "queued": True, "mode": mode_key, "label": label}


def queue_yami_icon_alias(scope: str = "yami", target: str = "cmn") -> dict[str, Any]:
    scope_key = str(scope or "yami").strip().lower()
    target_key = str(target or "cmn").strip().lower()
    if scope_key in MATERIAL_ALIAS_SCOPES:
        if target_key not in MATERIAL_ALIAS_TARGETS:
            target_key = "tek"
        scope_label = MATERIAL_ALIAS_SCOPES[scope_key][0]
        target_label = MATERIAL_ALIAS_TARGETS[target_key][0]
    elif scope_key in BOTTOM_ICON_SCOPES:
        if target_key not in BOTTOM_ICON_TARGETS:
            target_key = "tek"
        scope_label = BOTTOM_ICON_SCOPES[scope_key][0]
        target_label = BOTTOM_ICON_TARGETS[target_key][0]
    else:
        if scope_key not in ICON_ALIAS_SCOPES:
            scope_key = "yami"
        if target_key not in ICON_ALIAS_TARGETS:
            target_key = "cmn"
        scope_label = ICON_ALIAS_SCOPES[scope_key][0]
        target_label = ICON_ALIAS_TARGETS[target_key][0]
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_icon_alias", "scope": scope_key, "target": target_key})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = f"Yami icon/material alias queued: {scope_label} -> {target_label}"
    return {"ok": True, "queued": True, "scope": scope_key, "target": target_key, "label": f"{scope_label} -> {target_label}"}




def queue_yami_borrowed_icons() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_borrowed_icons"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "borrowed Yami icon plan queued"
    return {"ok": True, "queued": True, "label": "Yami 1=Yatterman-2, Yami 2=Tekkaman, Yami 3=Casshan"}



def queue_bottom_yami_trio_icons() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "bottom_yami_trio_icons"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "bottom carousel Yami trio icon probe queued"
    return {"ok": True, "queued": True, "label": "bottom blanks cycle Yatterman-2, Tekkaman, Casshan"}


def queue_yami_material_trio() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_material_trio"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "Yami material trio probe queued"
    return {"ok": True, "queued": True, "label": "material cycle Yami1=Yatterman-2, Yami2=Tekkaman, Yami3=Casshan"}


def queue_yami_0300_path_trio() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_0300_path_trio"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "Yami 0300 portrait path trio probe queued"
    return {"ok": True, "queued": True, "label": "0300 paths Yami1=Yatterman-2, Yami2=Tekkaman, Yami3=Casshan"}


def queue_yami_force_hover(slot_index: Any = 0x1B) -> dict[str, Any]:
    slot = _parse_slot_value(slot_index, 0x1B) & 0xFF
    cid = _SLOT_TO_ID.get(slot, 0x17)
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_force_hover", "slot": slot, "target": cid})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = f"Yami force-hover queued {_slot_label(slot, cid)}"
    return {"ok": True, "queued": True, "slot": f"0x{slot:02X}", "target_label": _char_label(cid)}


def queue_yami_force_focus(slot_index: Any = 0x1B) -> dict[str, Any]:
    slot = _parse_slot_value(slot_index, 0x1B) & 0xFF
    cid = _SLOT_TO_ID.get(slot, 0x17)
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_force_focus", "slot": slot, "target": cid})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = f"Yami full-focus queued {_slot_label(slot, cid)}"
    return {"ok": True, "queued": True, "slot": f"0x{slot:02X}", "target_label": _char_label(cid)}


def queue_roster_restore() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "restore"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "restore queued"
    return {"ok": True, "queued": True}


def _remember_original(addr: int, value: int | None = None) -> int | None:
    addr_i = int(addr) & 0xFFFFFFFF
    if value is None:
        value = _safe_read_u32be(addr_i)
    if value is None:
        return None
    if addr_i not in _ROSTER_ORIGINALS:
        _ROSTER_ORIGINALS[addr_i] = int(value) & 0xFFFFFFFF
    return int(value) & 0xFFFFFFFF


def _write_saved(addr: int, value: int) -> bool:
    original = _remember_original(addr)
    if original is None:
        return False
    return _safe_write_u32be(addr, value)


def _remember_original_bytes(addr: int, size: int) -> bytes | None:
    addr_i = int(addr) & 0xFFFFFFFF
    data = _safe_read(addr_i, int(size))
    if not data or len(data) < int(size):
        return None
    if addr_i not in _ROSTER_BYTE_ORIGINALS:
        _ROSTER_BYTE_ORIGINALS[addr_i] = bytes(data[: int(size)])
    return bytes(data[: int(size)])


def _write_bytes_saved(addr: int, data: bytes, expected: bytes | None = None, size: int | None = None) -> bool:
    addr_i = int(addr) & 0xFFFFFFFF
    payload = bytes(data)
    write_size = int(size or len(payload))
    original = _remember_original_bytes(addr_i, write_size)
    if original is None:
        return False
    if expected is not None and not original.startswith(bytes(expected)):
        with _LOCK:
            _ROSTER_STATE["last_error"] = (
                f"visual alias expected {bytes(expected)!r} at 0x{addr_i:08X}, "
                f"found {original!r}"
            )
        return False
    if len(payload) != write_size:
        payload = payload[:write_size].ljust(write_size, b"\x00")
    return _safe_write_bytes(addr_i, payload)


def _make_string_table_entry(text: bytes, size: int = VISUAL_ALIAS_ENTRY_SIZE) -> bytes:
    raw = bytes(text)
    if len(raw) > size - 2:
        raise ValueError(f"visual alias string too long for {size}-byte entry: {raw!r}")
    payload = bytearray(b"\x00" * size)
    payload[: len(raw)] = raw
    payload[-1] = len(raw) & 0xFF
    return bytes(payload)


def _make_fixed_c_string_field(text: bytes, size: int = ACTIVE_SHELL_STRING_FIELD_SIZE) -> bytes:
    raw = bytes(text)
    if len(raw) + 1 > int(size):
        raise ValueError(f"active shell string too long for {size}-byte field: {raw!r}")
    return raw + (b"\x00" * (int(size) - len(raw)))



def _make_short_tag_field(text: bytes, size: int = 4) -> bytes:
    raw = bytes(text)
    if len(raw) + 1 > int(size):
        raise ValueError(f"short tag too long for {size}-byte field: {raw!r}")
    return raw + (b"\x00" * (int(size) - len(raw)))



def _install_yami_ken_dupe_soft_frank() -> tuple[int, int]:
    wrote = 0
    failed = 0

    for addr in KEN_DUP_SAFE_MOF_FIELDS:
        payload = KEN_DUP_FRANK_MOF.ljust(8, b"\x00")
        expected = KEN_DUP_EXPECT_MOF.ljust(8, b"\x00")
        if _write_bytes_saved(addr, payload, expected=expected, size=8):
            wrote += 1
        else:
            failed += 1

    for addr in KEN_DUP_SAFE_SELECT_FIELDS:
        payload = KEN_DUP_FRANK_SELECT.ljust(12, b"\x00")
        expected = KEN_DUP_EXPECT_SELECT.ljust(12, b"\x00")
        if _write_bytes_saved(addr, payload, expected=expected, size=12):
            wrote += 1
        else:
            failed += 1

    for addr in KEN_DUP_SAFE_NAME_FIELDS:
        payload = KEN_DUP_FRANK_NAME.ljust(12, b"\x00")
        expected = KEN_DUP_EXPECT_NAME.ljust(12, b"\x00")
        if _write_bytes_saved(addr, payload, expected=expected, size=12):
            wrote += 1
        else:
            # These can already be changed by earlier probes. Count as optional.
            pass

    # Optional static icon table test: if the visible duplicate is really using
    # the Ken icon table entry, this should flip all Ken icons to Frank. If it
    # does not, we know the visible face is deeper than icon_* strings.
    icon_payload = _make_string_table_entry(KEN_DUP_FRANK_ICON, ICON_ALIAS_ENTRY_SIZE)
    icon_expected = _make_string_table_entry(KEN_DUP_EXPECT_ICON, ICON_ALIAS_ENTRY_SIZE)
    for addr in KEN_DUP_STATIC_ICON_FIELDS:
        if _write_bytes_saved(addr, icon_payload, expected=icon_expected, size=ICON_ALIAS_ENTRY_SIZE):
            wrote += 1
        else:
            pass

    with _LOCK:
        _ROSTER_STATE["ken_dupe_patch_installed"] = failed == 0
        _ROSTER_STATE["ken_dupe_patch_mode"] = "soft_noncentral"
        _ROSTER_STATE["restore_available"] = bool(_ROSTER_ORIGINALS or _ROSTER_BYTE_ORIGINALS)
        _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
    return wrote, failed


def _install_yami_ken_dupe_owner_frank(mode: str = "tag") -> tuple[int, int]:
    mode_key = str(mode or "tag").strip().lower()
    if mode_key not in {"tag", "pair"}:
        mode_key = "tag"
    wrote = 0
    failed = 0
    plan = [(KEN_DUP_OWNER_TAG_PTR, KEN_DUP_FRANK_STATIC_TAG_ADDR, "tag")]
    if mode_key == "pair":
        plan = [
            (KEN_DUP_OWNER_MOF_PTR, KEN_DUP_FRANK_STATIC_MOF_ADDR, "mof"),
            (KEN_DUP_OWNER_TAG_PTR, KEN_DUP_FRANK_STATIC_TAG_ADDR, "tag"),
        ]
    for addr, target, _label in plan:
        if _write_saved(addr, target):
            wrote += 1
        else:
            failed += 1
    with _LOCK:
        _ROSTER_STATE["ken_dupe_patch_installed"] = failed == 0
        _ROSTER_STATE["ken_dupe_patch_mode"] = f"owner_{mode_key}"
        _ROSTER_STATE["restore_available"] = bool(_ROSTER_ORIGINALS or _ROSTER_BYTE_ORIGINALS)
    return wrote, failed


def _install_yami_frank_face_lock(mode: str = "tag") -> tuple[int, int]:
    mode_key = str(mode or "tag").strip().lower()
    if mode_key not in {"tag", "pair"}:
        mode_key = "tag"

    wrote = 0
    failed = 0

    # First, keep all already-known label/icon layers pointed at Frank. These
    # are not expected to solve the close-up face alone, but they prevent other
    # UI layers from contradicting the owner-pointer test.
    try:
        icon_payload = _make_string_table_entry(b"icon_fra", ICON_ALIAS_ENTRY_SIZE)
        for source_key in FRANK_FACE_LOCK_ICON_KEYS:
            expected = _expected_icon_entry(source_key)
            for addr in ICON_TABLE_ENTRY_ADDRS.get(source_key, ()):  # tk1/tk2/tk3 -> icon_fra
                if _write_bytes_saved(addr, icon_payload, expected=expected, size=ICON_ALIAS_ENTRY_SIZE):
                    wrote += 1
                else:
                    failed += 1
    except Exception as exc:
        with _LOCK:
            _ROSTER_STATE["last_error"] = str(exc)
        failed += 1

    try:
        select_payload = _make_fixed_c_string_field(b"select_fra", ACTIVE_SHELL_STRING_FIELD_SIZE)
        name_payload = _make_fixed_c_string_field(b"name_fra", ACTIVE_SHELL_STRING_FIELD_SIZE)
        for addr in ACTIVE_SHELL_SELECT_FIELDS:
            if _write_bytes_saved(addr, select_payload, expected=None, size=ACTIVE_SHELL_STRING_FIELD_SIZE):
                wrote += 1
            else:
                failed += 1
        for addr in ACTIVE_SHELL_NAME_FIELDS:
            if _write_bytes_saved(addr, name_payload, expected=None, size=ACTIVE_SHELL_STRING_FIELD_SIZE):
                wrote += 1
            else:
                failed += 1
    except Exception as exc:
        with _LOCK:
            _ROSTER_STATE["last_error"] = str(exc)
        failed += 1

    for addr, expected, replacement, _label in FRANK_FACE_LOCK_FACE_NAME_PLAN:
        payload = bytes(replacement).ljust(len(expected), b"\x00")
        if _write_bytes_saved(addr, payload, expected=expected, size=len(expected)):
            wrote += 1
        else:
            # These are optional; if the current dump is not showing Ryu at this
            # exact face-resource block anymore, do not make the whole lock fail.
            pass

    # Critical part: do not touch 0x90818460/70 contents. Rewire the owner
    # pointer(s) so the selected face/material reader sees Frank as the donor.
    pointer_plan = FRANK_FACE_LOCK_POINTER_PAIR if mode_key == "pair" else FRANK_FACE_LOCK_POINTER_TAG_ONLY
    for addr, target, _label in pointer_plan:
        if _write_saved(addr, target):
            wrote += 1
        else:
            failed += 1

    with _LOCK:
        _ROSTER_STATE["frank_face_lock_installed"] = failed == 0
        _ROSTER_STATE["frank_face_lock_mode"] = mode_key
        _ROSTER_STATE["icon_alias_installed"] = failed == 0
        _ROSTER_STATE["icon_alias_scope"] = "Frank face owner pointer lock" if failed == 0 else ""
        _ROSTER_STATE["icon_alias_target"] = "Frank West" if failed == 0 else ""
        _ROSTER_STATE["restore_available"] = bool(_ROSTER_ORIGINALS or _ROSTER_BYTE_ORIGINALS)
        _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
    return wrote, failed

def _install_yami_far_donor_probe(donor: str = "fra") -> tuple[int, int]:
    # Hard-disabled after it froze cursor. Do not write active selected/material
    # banks here.
    with _LOCK:
        _ROSTER_STATE["icon_alias_installed"] = False
        _ROSTER_STATE["icon_alias_scope"] = "Far donor hard probe disabled"
        _ROSTER_STATE["icon_alias_target"] = str(donor)
        _ROSTER_STATE["last_error"] = "Far donor hard probe disabled: active 0x90818460/70 bank is unsafe."
    return 0, 0

def _install_yami_clone_table() -> tuple[int, int]:
    wrote = 0
    failed = 0
    for slot, cid, _name in YAMI_CLONE_SLOTS:
        addr = _roster_addr_for_slot(slot)
        if _write_saved(addr, cid):
            wrote += 1
        else:
            failed += 1
    with _LOCK:
        _ROSTER_STATE["clone_table_installed"] = failed == 0
    return wrote, failed


def _install_yami_clone_count() -> tuple[int, int]:
    wrote = 0
    failed = 0
    for addr, _label in ROSTER_COUNT_ADDRS:
        if _write_saved(addr, YAMI_CLONE_COUNT):
            wrote += 1
        else:
            failed += 1
    with _LOCK:
        _ROSTER_STATE["clone_count_installed"] = failed == 0
    return wrote, failed


def _install_yami_visual_alias(mode: str = "zero") -> tuple[int, int]:
    mode_key = str(mode or "zero").strip().lower()
    if mode_key not in VISUAL_ALIAS_PRESETS:
        mode_key = "zero"
    preset_label, entries = VISUAL_ALIAS_PRESETS[mode_key]
    wrote = 0
    failed = 0
    for addr, expected, replacement, _label in entries:
        try:
            payload = _make_string_table_entry(replacement, VISUAL_ALIAS_ENTRY_SIZE)
        except ValueError as exc:
            with _LOCK:
                _ROSTER_STATE["last_error"] = str(exc)
            failed += 1
            continue
        if _write_bytes_saved(addr, payload, expected=expected, size=VISUAL_ALIAS_ENTRY_SIZE):
            wrote += 1
        else:
            failed += 1
    with _LOCK:
        _ROSTER_STATE["visual_alias_installed"] = failed == 0
        _ROSTER_STATE["visual_alias_mode"] = preset_label if failed == 0 else ""
        _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
    return wrote, failed


def _install_yami_active_shell_alias(target: str = "cmn") -> tuple[int, int]:
    target_key = str(target or "cmn").strip().lower()
    if target_key not in ACTIVE_SHELL_SELECT_TARGETS:
        target_key = "cmn"
    target_label, select_text = ACTIVE_SHELL_SELECT_TARGETS[target_key]
    wrote = 0
    failed = 0
    try:
        select_payload = _make_fixed_c_string_field(select_text, ACTIVE_SHELL_STRING_FIELD_SIZE)
        # Keep the name plate harmless and stable while probing the wheel thumbnail.
        name_payload = _make_fixed_c_string_field(b"name_zer", ACTIVE_SHELL_STRING_FIELD_SIZE)
    except ValueError as exc:
        with _LOCK:
            _ROSTER_STATE["last_error"] = str(exc)
        return 0, 1

    for addr in ACTIVE_SHELL_SELECT_FIELDS:
        if _write_bytes_saved(addr, select_payload, expected=None, size=ACTIVE_SHELL_STRING_FIELD_SIZE):
            wrote += 1
        else:
            failed += 1
    for addr in ACTIVE_SHELL_NAME_FIELDS:
        if _write_bytes_saved(addr, name_payload, expected=None, size=ACTIVE_SHELL_STRING_FIELD_SIZE):
            wrote += 1
        else:
            failed += 1
    with _LOCK:
        _ROSTER_STATE["icon_alias_installed"] = failed == 0
        _ROSTER_STATE["icon_alias_scope"] = "Live Yami shell fallback strings" if failed == 0 else ""
        _ROSTER_STATE["icon_alias_target"] = target_label if failed == 0 else ""
        _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
    return wrote, failed


def _expected_icon_entry(key: str) -> bytes:
    return _make_string_table_entry(f"icon_{key}".encode("ascii"), ICON_ALIAS_ENTRY_SIZE)


def _install_yami_icon_alias(scope: str = "yami", target: str = "cmn") -> tuple[int, int]:
    scope_key = str(scope or "yami").strip().lower()
    target_key = str(target or "cmn").strip().lower()
    if scope_key in MATERIAL_ALIAS_SCOPES:
        return _install_material_alias(scope_key, target_key)
    if scope_key in BOTTOM_ICON_SCOPES:
        return _install_bottom_icon_alias(scope_key, target_key)
    if scope_key not in ICON_ALIAS_SCOPES:
        scope_key = "yami"
    if target_key not in ICON_ALIAS_TARGETS:
        target_key = "cmn"
    scope_label, source_keys = ICON_ALIAS_SCOPES[scope_key]
    if scope_key == "active_shell":
        return _install_yami_active_shell_alias(target_key)
    target_label, target_text = ICON_ALIAS_TARGETS[target_key]
    wrote = 0
    failed = 0
    try:
        payload = _make_string_table_entry(target_text, ICON_ALIAS_ENTRY_SIZE)
    except ValueError as exc:
        with _LOCK:
            _ROSTER_STATE["last_error"] = str(exc)
        return 0, 1
    for source_key in source_keys:
        addrs = ICON_TABLE_ENTRY_ADDRS.get(source_key, ())
        expected = _expected_icon_entry(source_key)
        for addr in addrs:
            if _write_bytes_saved(addr, payload, expected=expected, size=ICON_ALIAS_ENTRY_SIZE):
                wrote += 1
            else:
                failed += 1
    with _LOCK:
        _ROSTER_STATE["icon_alias_installed"] = failed == 0
        _ROSTER_STATE["icon_alias_scope"] = scope_label if failed == 0 else ""
        _ROSTER_STATE["icon_alias_target"] = target_label if failed == 0 else ""
        _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
    return wrote, failed







def _make_material_field(text: bytes) -> bytes:
    raw = bytes(text)
    if len(raw) > MATERIAL_FIELD_SIZE:
        raise ValueError(f"material string too long for {MATERIAL_FIELD_SIZE}-byte field: {raw!r}")
    return raw[:MATERIAL_FIELD_SIZE].ljust(MATERIAL_FIELD_SIZE, b"\x00")


def _install_material_alias(scope: str, target: str) -> tuple[int, int]:
    scope_key = str(scope or "material_bottom_core").strip().lower()
    target_key = str(target or "tek").strip().lower()
    if scope_key not in MATERIAL_ALIAS_SCOPES:
        scope_key = "material_bottom_core"
    if target_key not in MATERIAL_ALIAS_TARGETS:
        target_key = "tek"
    scope_label, addrs = MATERIAL_ALIAS_SCOPES[scope_key]
    target_label, target_text = MATERIAL_ALIAS_TARGETS[target_key]
    try:
        payload = _make_material_field(target_text)
    except ValueError as exc:
        with _LOCK:
            _ROSTER_STATE["last_error"] = str(exc)
        return 0, 1
    wrote = 0
    failed = 0
    for addr in addrs:
        if _write_bytes_saved(addr, payload, expected=None, size=MATERIAL_FIELD_SIZE):
            wrote += 1
        else:
            failed += 1
    with _LOCK:
        _ROSTER_STATE["icon_alias_installed"] = failed == 0
        _ROSTER_STATE["icon_alias_scope"] = scope_label if failed == 0 else ""
        _ROSTER_STATE["icon_alias_target"] = target_label if failed == 0 else ""
        _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
    return wrote, failed


def _install_yami_material_trio() -> tuple[int, int]:
    wrote = 0
    failed = 0
    for addr, text, _label in MATERIAL_YAMI_TRIO_PLAN:
        try:
            payload = _make_material_field(text)
        except ValueError as exc:
            with _LOCK:
                _ROSTER_STATE["last_error"] = str(exc)
            failed += 1
            continue
        if _write_bytes_saved(addr, payload, expected=None, size=MATERIAL_FIELD_SIZE):
            wrote += 1
        else:
            failed += 1
    with _LOCK:
        _ROSTER_STATE["icon_alias_installed"] = failed == 0
        _ROSTER_STATE["icon_alias_scope"] = "Yami material trio" if failed == 0 else ""
        _ROSTER_STATE["icon_alias_target"] = "Yami1=Yatterman-2, Yami2=Tekkaman, Yami3=Casshan" if failed == 0 else ""
        _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
    return wrote, failed


def _install_yami_0300_path_trio() -> tuple[int, int]:
    wrote = 0
    failed = 0
    for addr, expected, replacement, _label in RESOURCE_0300_YAMI_TRIO_PLAN:
        if len(expected) != RESOURCE_0300_FIELD_SIZE or len(replacement) != RESOURCE_0300_FIELD_SIZE:
            with _LOCK:
                _ROSTER_STATE["last_error"] = "0300 path replacement size mismatch"
            failed += 1
            continue
        if _write_bytes_saved(addr, replacement, expected=expected, size=RESOURCE_0300_FIELD_SIZE):
            wrote += 1
        else:
            # If a previous button/test already changed the exact expected text,
            # still try an unconditional saved write so Restore can return it.
            if _write_bytes_saved(addr, replacement, expected=None, size=RESOURCE_0300_FIELD_SIZE):
                wrote += 1
            else:
                failed += 1
    with _LOCK:
        _ROSTER_STATE["icon_alias_installed"] = failed == 0
        _ROSTER_STATE["icon_alias_scope"] = "Yami 0300 portrait path trio" if failed == 0 else ""
        _ROSTER_STATE["icon_alias_target"] = "Yami1=Yatterman-2, Yami2=Tekkaman, Yami3=Casshan" if failed == 0 else ""
        _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
    return wrote, failed


def _make_bottom_icon_field(text: bytes) -> bytes:
    raw = bytes(text)
    if len(raw) > BOTTOM_ICON_FIELD_SIZE:
        raise ValueError(f"bottom icon string too long for {BOTTOM_ICON_FIELD_SIZE}-byte field: {raw!r}")
    return raw[:BOTTOM_ICON_FIELD_SIZE].ljust(BOTTOM_ICON_FIELD_SIZE, b"\x00")


def _install_bottom_icon_alias(scope: str, target: str) -> tuple[int, int]:
    scope_key = str(scope or "bottom_late_blanks").strip().lower()
    target_key = str(target or "tek").strip().lower()
    if scope_key not in BOTTOM_ICON_SCOPES:
        scope_key = "bottom_late_blanks"
    if target_key not in BOTTOM_ICON_TARGETS:
        target_key = "tek"
    scope_label, addrs = BOTTOM_ICON_SCOPES[scope_key]
    target_label, target_text = BOTTOM_ICON_TARGETS[target_key]
    try:
        payload = _make_bottom_icon_field(target_text)
    except ValueError as exc:
        with _LOCK:
            _ROSTER_STATE["last_error"] = str(exc)
        return 0, 1
    wrote = 0
    failed = 0
    for addr in addrs:
        if _write_bytes_saved(addr, payload, expected=BOTTOM_ICON_EXPECT_BLANK, size=BOTTOM_ICON_FIELD_SIZE):
            wrote += 1
        else:
            failed += 1
    with _LOCK:
        _ROSTER_STATE["icon_alias_installed"] = failed == 0
        _ROSTER_STATE["icon_alias_scope"] = scope_label if failed == 0 else ""
        _ROSTER_STATE["icon_alias_target"] = target_label if failed == 0 else ""
        _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
    return wrote, failed


def _install_bottom_yami_trio_icons() -> tuple[int, int]:
    wrote = 0
    failed = 0
    for addr, text, _label in BOTTOM_YAMI_TRIO_PLAN:
        try:
            payload = _make_bottom_icon_field(text)
        except ValueError as exc:
            with _LOCK:
                _ROSTER_STATE["last_error"] = str(exc)
            failed += 1
            continue
        if _write_bytes_saved(addr, payload, expected=BOTTOM_ICON_EXPECT_BLANK, size=BOTTOM_ICON_FIELD_SIZE):
            wrote += 1
        else:
            failed += 1
    with _LOCK:
        _ROSTER_STATE["icon_alias_installed"] = failed == 0
        _ROSTER_STATE["icon_alias_scope"] = "Bottom carousel Yami trio blanks" if failed == 0 else ""
        _ROSTER_STATE["icon_alias_target"] = "ya2 / tek / cas cycle" if failed == 0 else ""
        _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
    return wrote, failed


def _install_yami_borrowed_icons() -> tuple[int, int]:
    wrote = 0
    failed = 0

    # 1) Patch the native hidden icon labels in both loaded icon tables.
    # This is harmless/reversible and keeps Yami's character ID path intact.
    for plan in BORROWED_YAMI_ICON_PLAN:
        source_key = str(plan["yami_key"])
        donor_icon = bytes(plan["icon"])
        try:
            payload = _make_string_table_entry(donor_icon, ICON_ALIAS_ENTRY_SIZE)
            expected = _expected_icon_entry(source_key)
        except Exception as exc:
            with _LOCK:
                _ROSTER_STATE["last_error"] = str(exc)
            failed += 1
            continue
        for addr in ICON_TABLE_ENTRY_ADDRS.get(source_key, ()):
            if _write_bytes_saved(addr, payload, expected=expected, size=ICON_ALIAS_ENTRY_SIZE):
                wrote += 1
            else:
                failed += 1

    # 2) Patch live hidden-shell select fallback groups to differing borrowed
    # select plates. If only one hidden shell is currently active, this may only
    # affect that one; once we expose/clone more shells, the mapping is ready.
    for group_i, group in enumerate(ACTIVE_SHELL_SELECT_GROUPS):
        plan = BORROWED_YAMI_ICON_PLAN[group_i % len(BORROWED_YAMI_ICON_PLAN)]
        try:
            payload = _make_fixed_c_string_field(bytes(plan["select"]), ACTIVE_SHELL_STRING_FIELD_SIZE)
        except ValueError as exc:
            with _LOCK:
                _ROSTER_STATE["last_error"] = str(exc)
            failed += len(group)
            continue
        for addr in group:
            if _write_bytes_saved(addr, payload, expected=None, size=ACTIVE_SHELL_STRING_FIELD_SIZE):
                wrote += 1
            else:
                failed += 1

    with _LOCK:
        _ROSTER_STATE["icon_alias_installed"] = failed == 0
        _ROSTER_STATE["icon_alias_scope"] = "Borrowed Yami trio" if failed == 0 else ""
        _ROSTER_STATE["icon_alias_target"] = "Yami1=Yatterman-2, Yami2=Tekkaman, Yami3=Casshan" if failed == 0 else ""
        _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
    return wrote, failed



def _install_yami_face_resource_probe(donor: str) -> tuple[int, int]:
    donor_key = str(donor or "gac").strip().lower()
    if donor_key not in FACE_RESOURCE_DONORS:
        donor_key = "gac"
    donor_label, replacements = FACE_RESOURCE_DONORS[donor_key]
    wrote = 0
    failed = 0
    for (addr, expected, _label), replacement in zip(FACE_RESOURCE_FIELD_PLAN, replacements):
        size = len(expected)
        if len(replacement) != size:
            with _LOCK:
                _ROSTER_STATE["last_error"] = f"face resource replacement size mismatch: {replacement!r} for size {size}"
            failed += 1
            continue
        if _write_bytes_saved(addr, replacement, expected=expected, size=size):
            wrote += 1
        else:
            # If another donor was already installed, let probe chain continue.
            if _write_bytes_saved(addr, replacement, expected=None, size=size):
                wrote += 1
            else:
                failed += 1
    with _LOCK:
        _ROSTER_STATE["icon_alias_installed"] = failed == 0
        _ROSTER_STATE["icon_alias_scope"] = "Loaded close-up face resource names" if failed == 0 else ""
        _ROSTER_STATE["icon_alias_target"] = donor_label if failed == 0 else ""
        _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
    return wrote, failed

def _read_ascii_preview(addr: int, size: int = 96) -> str:
    data = _safe_read(int(addr), int(size)) or b""
    out = []
    for b in bytes(data):
        if 32 <= b < 127:
            out.append(chr(b))
        elif b == 0:
            out.append(".")
        else:
            out.append(".")
    return "".join(out)


def _read_u32_list(addr: int, count: int = 8) -> list[str]:
    out: list[str] = []
    for i in range(int(count)):
        v = _safe_read_u32be(int(addr) + i * 4)
        out.append(_hex(v) if v is not None else "")
    return out


def _capture_yami_pane_probe_snapshot() -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for addr in PANE_PROBE_RECORD_ADDRS:
        records.append({
            "addr": _hex(addr),
            "u32": _read_u32_list(addr, 8),
            "ascii": _read_ascii_preview(addr, 96),
        })
    snap = {
        "selector": _read_roster_selector_snapshot(),
        "active_bank_90818460": _read_ascii_preview(0x90818460, 160),
        "owner_record_90844A00_u32": _read_u32_list(0x90844A00, 32),
        "ken_duplicate_mof_fields": [
            {"addr": _hex(a), "ascii": _read_ascii_preview(a, 16)} for a in KEN_DUP_SAFE_MOF_FIELDS
        ],
        "owner_ptrs": {
            "mof_ptr_90844A18": _hex(_safe_read_u32be(KEN_DUP_OWNER_MOF_PTR)),
            "tag_ptr_90844A1C": _hex(_safe_read_u32be(KEN_DUP_OWNER_TAG_PTR)),
        },
        "face_blocks": {
            "ryu_header": _read_u32_list(0x921FE720, 8),
            "gac_header": _read_u32_list(0x92202140, 8),
            "chu_header": _read_u32_list(0x92205180, 8),
        },
        "records": records,
    }
    with _LOCK:
        _ROSTER_STATE["pane_probe_snapshot"] = snap
    return snap


def _install_yami_face_block_copy(donor: str = "gac") -> tuple[int, int]:
    donor_key = str(donor or "gac").strip().lower()
    if donor_key not in FACE_BLOCK_COPY_DONORS:
        donor_key = "gac"
    donor_label, donor_addr, donor_size = FACE_BLOCK_COPY_DONORS[donor_key]
    donor_bytes = _safe_read(int(donor_addr), int(donor_size))
    if not donor_bytes or len(donor_bytes) != int(donor_size):
        with _LOCK:
            _ROSTER_STATE["last_error"] = f"face-block donor read failed at 0x{int(donor_addr):08X}"
        return 0, 1
    payload = bytes(donor_bytes).ljust(FACE_BLOCK_ACTIVE_RYU_PAYLOAD_SIZE, b"\x00")[:FACE_BLOCK_ACTIVE_RYU_PAYLOAD_SIZE]
    if _write_bytes_saved(FACE_BLOCK_ACTIVE_RYU_PAYLOAD_ADDR, payload, expected=None, size=FACE_BLOCK_ACTIVE_RYU_PAYLOAD_SIZE):
        with _LOCK:
            _ROSTER_STATE["face_block_copy_installed"] = True
            _ROSTER_STATE["face_block_copy_donor"] = donor_label
            _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
        return 1, 0
    return 0, 1


def _force_yami_hover(slot: int, target: int) -> tuple[int, int]:
    wrote = 0
    failed = 0
    for addr in CURSOR_INDEX_ADDRS:
        if _write_saved(addr, int(slot) & 0xFF):
            wrote += 1
        else:
            failed += 1
    for addr in HOVER_CHAR_ID_ADDRS:
        if _write_saved(addr, int(target) & 0xFFFFFFFF):
            wrote += 1
        else:
            failed += 1
    with _LOCK:
        _ROSTER_STATE["last_clone_slot"] = _slot_label(slot, target)
    return wrote, failed


def _force_yami_focus(slot: int, target: int) -> tuple[int, int]:
    wrote, failed = _force_yami_hover(slot, target)
    for addr in FOCUS_CHAR_ID_ADDRS:
        if _write_saved(addr, int(target) & 0xFFFFFFFF):
            wrote += 1
        else:
            failed += 1
    with _LOCK:
        _ROSTER_STATE["last_clone_slot"] = _slot_label(slot, target) + " full-focus"
    return wrote, failed


def _do_restore() -> dict[str, int]:
    restored = 0
    failed = 0

    byte_originals = dict(_ROSTER_BYTE_ORIGINALS)
    for addr, data in byte_originals.items():
        if _safe_write_bytes(int(addr), bytes(data)):
            restored += 1
        else:
            failed += 1

    originals = dict(_ROSTER_ORIGINALS)
    for addr, value in originals.items():
        if _safe_write_u32be(int(addr), int(value)):
            restored += 1
        else:
            failed += 1

    if failed == 0:
        _ROSTER_ORIGINALS.clear()
        _ROSTER_BYTE_ORIGINALS.clear()
        with _LOCK:
            _ROSTER_STATE["clone_table_installed"] = False
            _ROSTER_STATE["clone_count_installed"] = False
            _ROSTER_STATE["visual_alias_installed"] = False
            _ROSTER_STATE["icon_alias_installed"] = False
            _ROSTER_STATE["icon_alias_scope"] = ""
            _ROSTER_STATE["icon_alias_target"] = ""
            _ROSTER_STATE["byte_restore_available"] = False
            _ROSTER_STATE["last_clone_slot"] = ""
            _ROSTER_STATE["face_block_copy_installed"] = False
            _ROSTER_STATE["face_block_copy_donor"] = ""
            _ROSTER_STATE["frank_face_lock_installed"] = False
            _ROSTER_STATE["frank_face_lock_mode"] = ""
            _ROSTER_STATE["ken_dupe_patch_installed"] = False
            _ROSTER_STATE["ken_dupe_patch_mode"] = ""
            _ROSTER_STATE["owned_bank_installed"] = False
            _ROSTER_STATE["owned_bank_addr"] = ""
            _ROSTER_STATE["visual_table_patch_installed"] = False
            _ROSTER_STATE["visual_table_patch_mode"] = ""
            _ROSTER_STATE["extra_characters_enabled"] = False
            _ROSTER_STATE["extra_characters_requested"] = False
            _ROSTER_STATE["extra_characters_mode"] = ""
            _ROSTER_STATE["solo_team_enabled"] = False
            _ROSTER_STATE["solo_team_requested"] = False
            _ROSTER_STATE["solo_team_mode"] = ""
    with _LOCK:
        _ROSTER_STATE["restored"] = int(_ROSTER_STATE.get("restored", 0) or 0) + restored
        _ROSTER_STATE["failed"] = int(_ROSTER_STATE.get("failed", 0) or 0) + failed
        _ROSTER_STATE["restore_available"] = bool(_ROSTER_ORIGINALS or _ROSTER_BYTE_ORIGINALS)
        _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
        _ROSTER_STATE["last_action"] = f"restore done restored={restored} failed={failed}"
        _ROSTER_STATE["last_error"] = "" if failed == 0 else f"restore failed for {failed} address(es)"
        try:
            _ROSTER_STATE["last_snapshot"] = _read_roster_selector_snapshot()
        except Exception:
            pass
    return {"restored": restored, "failed": failed}


def _tick_roster_actions() -> None:
    _tick_extra_characters_request()
    _tick_solo_team_request()
    with _LOCK:
        actions = list(_ROSTER_QUEUE)
        _ROSTER_QUEUE.clear()
        _ROSTER_STATE["queued"] = 0
    if not actions:
        return

    for action in actions:
        op = str(action.get("op") or "")
        try:
            if op == "snapshot":
                snap = _read_roster_selector_snapshot()
                with _LOCK:
                    _ROSTER_STATE["last_snapshot"] = snap
                    _ROSTER_STATE["last_action"] = "selector snapshot captured"
                    _ROSTER_STATE["last_error"] = ""
                continue

            if op == "restore":
                _do_restore()
                continue

            if op in (
                "yami_clone_table",
                "yami_clone_count",
                "yami_clone_all",
                "yami_force_hover",
                "yami_force_focus",
                "yami_visual_alias",
                "yami_shell_attempt",
                "yami_icon_alias",
                "yami_borrowed_icons",
                "bottom_yami_trio_icons",
                "yami_material_trio",
                "yami_0300_path_trio",
                "yami_far_donor_probe",
                "yami_face_resource_probe",
                "yami_face_block_copy",
                "yami_pane_probe_snapshot",
                "yami_frank_face_lock",
                "yami_ken_dupe_soft_frank",
                "yami_ken_dupe_owner_frank",
                "yami_owned_frank_bank",
                "yami_owned_bank_snapshot",
                "yami_visual_table_snapshot",
                "yami_visual_table_frank_append",
                "yami_visual_table_native_append",
                "yami_visual_table_three_donor_append",
                "extra_chars_on",
                "extra_chars_off",
                "solo_team_on",
                "solo_team_off",
            ):
                wrote = 0
                failed = 0
                if op in ("yami_clone_table", "yami_clone_all", "yami_shell_attempt"):
                    w, f = _install_yami_clone_table()
                    wrote += w
                    failed += f
                if op in ("yami_clone_count", "yami_clone_all", "yami_shell_attempt"):
                    w, f = _install_yami_clone_count()
                    wrote += w
                    failed += f
                if op in ("yami_visual_alias", "yami_shell_attempt"):
                    mode = str(action.get("mode", "zero") or "zero")
                    w, f = _install_yami_visual_alias(mode)
                    wrote += w
                    failed += f
                if op == "yami_icon_alias":
                    scope = str(action.get("scope", "yami") or "yami")
                    target = str(action.get("target", "cmn") or "cmn")
                    w, f = _install_yami_icon_alias(scope, target)
                    wrote += w
                    failed += f
                if op == "yami_borrowed_icons":
                    w, f = _install_yami_borrowed_icons()
                    wrote += w
                    failed += f
                if op == "bottom_yami_trio_icons":
                    w, f = _install_bottom_yami_trio_icons()
                    wrote += w
                    failed += f
                if op == "yami_material_trio":
                    w, f = _install_yami_material_trio()
                    wrote += w
                    failed += f
                if op == "yami_0300_path_trio":
                    w, f = _install_yami_0300_path_trio()
                    wrote += w
                    failed += f
                if op == "yami_far_donor_probe":
                    w, f = _install_yami_far_donor_probe(str(action.get("donor") or "fra"))
                    wrote += w
                    failed += f
                if op == "yami_face_resource_probe":
                    w, f = _install_yami_face_resource_probe(str(action.get("donor") or "gac"))
                    wrote += w
                    failed += f
                if op == "yami_face_block_copy":
                    w, f = _install_yami_face_block_copy(str(action.get("donor") or "gac"))
                    wrote += w
                    failed += f
                if op == "yami_pane_probe_snapshot":
                    _capture_yami_pane_probe_snapshot()
                if op == "yami_frank_face_lock":
                    w, f = _install_yami_frank_face_lock(str(action.get("mode") or "tag"))
                    wrote += w
                    failed += f
                if op == "yami_ken_dupe_soft_frank":
                    w, f = _install_yami_ken_dupe_soft_frank()
                    wrote += w
                    failed += f
                if op == "yami_ken_dupe_owner_frank":
                    w, f = _install_yami_ken_dupe_owner_frank(str(action.get("mode") or "tag"))
                    wrote += w
                    failed += f
                if op == "yami_owned_frank_bank":
                    w, f = _install_yami_owned_frank_bank()
                    wrote += w
                    failed += f
                if op == "yami_owned_bank_snapshot":
                    _capture_yami_owned_bank_snapshot()
                if op == "yami_visual_table_snapshot":
                    _visual_table_snapshot()
                if op == "yami_visual_table_frank_append":
                    w, f = _install_yami_visual_table_append(VISUAL_TABLE_FRANK_FACE_APPEND_INDEX, "Frank visual indices + Yami char IDs")
                    wrote += w
                    failed += f
                if op == "yami_visual_table_native_append":
                    w, f = _install_yami_visual_table_append(VISUAL_TABLE_NATIVE_YAMI_APPEND_INDEX, "native Yami visual indices + Yami char IDs")
                    wrote += w
                    failed += f
                if op == "yami_visual_table_three_donor_append":
                    w, f = _install_yami_visual_table_append(VISUAL_TABLE_THREE_DONOR_APPEND_INDEX, VISUAL_TABLE_THREE_DONOR_LABEL)
                    wrote += w
                    failed += f
                if op == "extra_chars_on":
                    status = _update_extra_guard_state()
                    if status.get("active"):
                        w, f = _install_extra_characters_on()
                        wrote += w
                        failed += f
                    else:
                        with _LOCK:
                            _ROSTER_STATE["extra_characters_requested"] = True
                            _ROSTER_STATE["extra_characters_enabled"] = False
                            _ROSTER_STATE["last_action"] = "Extra characters ON deferred; not on character select"
                            _ROSTER_STATE["last_error"] = ""
                if op == "extra_chars_off":
                    status = _update_extra_guard_state()
                    if status.get("active"):
                        w, f = _restore_extra_roster_rows_only()
                        wrote += w
                        failed += f
                        with _LOCK:
                            _ROSTER_STATE["extra_characters_requested"] = False
                            _ROSTER_STATE["extra_characters_enabled"] = False
                            _ROSTER_STATE["extra_characters_mode"] = ""
                        if not bool(_ROSTER_STATE.get("solo_team_requested")):
                            w, f = _restore_extra_profile_rows_only()
                            wrote += w
                            failed += f
                    else:
                        with _LOCK:
                            _ROSTER_STATE["extra_characters_requested"] = False
                            _ROSTER_STATE["extra_characters_enabled"] = False
                            _ROSTER_STATE["extra_characters_mode"] = ""
                            _ROSTER_STATE["last_action"] = "Extra characters OFF deferred; not on character select"
                            _ROSTER_STATE["last_error"] = ""
                if op == "solo_team_on":
                    status = _update_extra_guard_state()
                    if status.get("active"):
                        w, f = _install_extra_profile_rows_only("Solo-team profile rows")
                        wrote += w
                        failed += f
                        with _LOCK:
                            _ROSTER_STATE["solo_team_enabled"] = f == 0
                            _ROSTER_STATE["solo_team_mode"] = "Profile-row solo-team helper"
                    else:
                        with _LOCK:
                            _ROSTER_STATE["solo_team_requested"] = True
                            _ROSTER_STATE["solo_team_enabled"] = False
                            _ROSTER_STATE["solo_team_guard"] = "armed; waiting for character select"
                if op == "solo_team_off":
                    status = _update_extra_guard_state()
                    if status.get("active") and not bool(_ROSTER_STATE.get("extra_characters_requested")):
                        w, f = _restore_extra_profile_rows_only()
                        wrote += w
                        failed += f
                    with _LOCK:
                        _ROSTER_STATE["solo_team_requested"] = False
                        _ROSTER_STATE["solo_team_enabled"] = False
                        _ROSTER_STATE["solo_team_mode"] = ""
                if op == "yami_force_hover":
                    slot = int(action.get("slot", 0x1B)) & 0xFF
                    target = int(action.get("target", _SLOT_TO_ID.get(slot, 0x17))) & 0xFFFFFFFF
                    w, f = _force_yami_hover(slot, target)
                    wrote += w
                    failed += f
                if op == "yami_force_focus":
                    slot = int(action.get("slot", 0x1B)) & 0xFF
                    target = int(action.get("target", _SLOT_TO_ID.get(slot, 0x17))) & 0xFFFFFFFF
                    w, f = _force_yami_focus(slot, target)
                    wrote += w
                    failed += f
                snap = _read_roster_selector_snapshot()
                with _LOCK:
                    _ROSTER_STATE["patches"] = int(_ROSTER_STATE.get("patches", 0) or 0) + wrote
                    _ROSTER_STATE["failed"] = int(_ROSTER_STATE.get("failed", 0) or 0) + failed
                    _ROSTER_STATE["restore_available"] = bool(_ROSTER_ORIGINALS or _ROSTER_BYTE_ORIGINALS)
                    _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
                    _ROSTER_STATE["last_action"] = f"{op} done wrote={wrote} failed={failed}"
                    _ROSTER_STATE["last_error"] = "" if failed == 0 else f"{op} failed for {failed} write(s)"
                    _ROSTER_STATE["last_snapshot"] = snap
                continue

            if op == "patch_current":
                idx = _safe_read_u32be(0x809BCEA0)
                if idx is None or not (0 <= int(idx) <= 0x40):
                    raise RuntimeError("could not read sane current hover index")
                slot = int(idx) & 0xFF
                target = int(action.get("target", 0x0D)) & 0xFFFFFFFF
                addr = _roster_addr_for_slot(slot)
            elif op == "patch_slot":
                slot = int(action.get("slot", 0x1A)) & 0xFF
                target = int(action.get("target", 0x0D)) & 0xFFFFFFFF
                addr = _roster_addr_for_slot(slot)
            else:
                continue

            original = _safe_read_u32be(addr)
            if original is None:
                raise RuntimeError(f"read failed at 0x{addr:08X}")
            if addr not in _ROSTER_ORIGINALS:
                _ROSTER_ORIGINALS[addr] = int(original)
            if not _safe_write_u32be(addr, target):
                raise RuntimeError(f"write failed at 0x{addr:08X}")

            snap = _read_roster_selector_snapshot()
            with _LOCK:
                _ROSTER_STATE["patches"] = int(_ROSTER_STATE.get("patches", 0) or 0) + 1
                _ROSTER_STATE["restore_available"] = bool(_ROSTER_ORIGINALS)
                _ROSTER_STATE["last_action"] = (
                    f"patched {_slot_label(slot, original)} at 0x{addr:08X}: "
                    f"{_char_label(original)} -> {_char_label(target)}"
                )
                _ROSTER_STATE["last_error"] = ""
                _ROSTER_STATE["last_snapshot"] = snap
        except Exception as e:
            with _LOCK:
                _ROSTER_STATE["failed"] = int(_ROSTER_STATE.get("failed", 0) or 0) + 1
                _ROSTER_STATE["last_error"] = repr(e)
                _ROSTER_STATE["last_action"] = f"{op} failed"


def get_roster_patch_state() -> dict[str, Any]:
    with _LOCK:
        state = dict(_ROSTER_STATE)
        state["restore_available"] = bool(_ROSTER_ORIGINALS or _ROSTER_BYTE_ORIGINALS)
        state["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
        state["originals"] = {
            f"0x{k:08X}": _char_label(v) for k, v in _ROSTER_ORIGINALS.items()
        }
        state["byte_originals"] = {
            f"0x{k:08X}": bytes(v).hex(" ") for k, v in _ROSTER_BYTE_ORIGINALS.items()
        }
        state["roster_slots"] = get_roster_slot_choices()
        state["target_chars"] = get_roster_char_choices()
        state["roster_base"] = f"0x{ROSTER_TABLE_BASE:08X}"
        state["clone_slots"] = [
            f"{name} clone slot 0x{slot:02X} (ID 0x{cid:02X})"
            for slot, cid, name in YAMI_CLONE_SLOTS
        ]
        state["clone_count"] = f"0x{YAMI_CLONE_COUNT:02X}"
        state["count_addrs"] = {f"0x{addr:08X}": label for addr, label in ROSTER_COUNT_ADDRS}
        state["visual_alias_presets"] = {
            key: {
                "label": preset_label,
                "entries": [
                    {"addr": f"0x{addr:08X}", "from": old.decode("ascii"), "to": new.decode("ascii"), "label": label}
                    for addr, old, new, label in entries
                ],
            }
            for key, (preset_label, entries) in VISUAL_ALIAS_PRESETS.items()
        }
        state["icon_alias_scopes"] = {
            key: {"label": label, "entries": list(entries)}
            for key, (label, entries) in {**ICON_ALIAS_SCOPES, **BOTTOM_ICON_SCOPES, **MATERIAL_ALIAS_SCOPES}.items()
        }
        state["icon_alias_targets"] = {
            key: {"label": label, "text": text.decode("ascii")}
            for key, (label, text) in {**ICON_ALIAS_TARGETS, **BOTTOM_ICON_TARGETS, **MATERIAL_ALIAS_TARGETS}.items()
        }
        state["material_yami_trio_plan"] = [
            {"addr": f"0x{addr:08X}", "text": text.decode("ascii"), "label": label}
            for addr, text, label in MATERIAL_YAMI_TRIO_PLAN
        ]
        state["face_resource_donors"] = {k: v[0] for k, v in FACE_RESOURCE_DONORS.items()}
        state["face_block_copy_donors"] = {k: v[0] for k, v in FACE_BLOCK_COPY_DONORS.items()}
        state["face_resource_plan"] = [
            {"addr": _hex(addr), "expected": expected.decode("ascii", "replace"), "label": label}
            for addr, expected, label in FACE_RESOURCE_FIELD_PLAN
        ]
        state["resource_0300_yami_trio_plan"] = [
            {
                "addr": f"0x{addr:08X}",
                "expected": expected.decode("ascii"),
                "replacement": replacement.decode("ascii"),
                "label": label,
            }
            for addr, expected, replacement, label in RESOURCE_0300_YAMI_TRIO_PLAN
        ]
        state["borrowed_yami_icon_plan"] = [
            {
                "yami": str(plan["yami_label"]),
                "native": str(plan["yami_key"]),
                "donor": str(plan["donor_label"]),
                "icon": bytes(plan["icon"]).decode("ascii"),
                "select": bytes(plan["select"]).decode("ascii"),
                "name": bytes(plan["name"]).decode("ascii"),
            }
            for plan in BORROWED_YAMI_ICON_PLAN
        ]
    return state


def tick_char_test() -> None:
    _tick_roster_actions()


def start_char_test(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {"ok": False, "disabled": True, "error": "Only roster table patch is enabled in this build."}


def stop_char_test() -> dict[str, Any]:
    return {"ok": True, "running": False}


def restore_char_test() -> dict[str, Any]:
    result = _do_restore()
    return {"ok": result.get("failed", 0) == 0, "roster_restore": result}


def get_char_test_state() -> dict[str, Any]:
    roster_state = get_roster_patch_state()
    requested = bool(roster_state.get("extra_characters_requested"))
    applied = bool(roster_state.get("extra_characters_enabled"))
    solo_requested = bool(roster_state.get("solo_team_requested"))
    solo_applied = bool(roster_state.get("solo_team_enabled"))
    return {
        "running": requested,
        "mode": "extra_characters" if requested else "extra_characters_off",
        "samples": 0,
        "changes": 0,
        "last_error": str(roster_state.get("last_error") or ""),
        "roster_patch": roster_state,
        "extra_characters_enabled": applied,
        "extra_characters_requested": requested,
        "extra_characters_select_active": bool(roster_state.get("extra_characters_select_active")),
        "extra_characters_patch_present": bool(roster_state.get("extra_characters_patch_present")),
        "solo_team_enabled": solo_applied,
        "solo_team_requested": solo_requested,
    }
