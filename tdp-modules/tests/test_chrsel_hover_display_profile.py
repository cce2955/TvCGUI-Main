"""Contracts for the live Character Select large-card presentation cache."""
from __future__ import annotations

import unittest

import tvcgui.features.character_select.runtime as runtime


class _VirtualMemory:
    def __init__(self) -> None:
        self.words: dict[int, int] = {}
        self.writes: list[tuple[int, int]] = []

    def read_u32(self, addr: int) -> int | None:
        return self.words.get(int(addr))

    def write_u32(self, addr: int, value: int) -> bool:
        self.words[int(addr)] = int(value) & 0xFFFFFFFF
        self.writes.append((int(addr), int(value) & 0xFFFFFFFF))
        return True


class CharacterSelectHoverDisplayProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mem = _VirtualMemory()
        self.saved = {
            "_safe_read_u32be": runtime._safe_read_u32be,
            "_safe_write_u32be": runtime._safe_write_u32be,
            "_select_screen_status": runtime._select_screen_status,
            "state": dict(runtime._ROSTER_STATE),
            "session": dict(runtime._YAMI_HOVER_DISPLAY_PROFILE_SESSION),
        }
        runtime._safe_read_u32be = self.mem.read_u32
        runtime._safe_write_u32be = self.mem.write_u32
        runtime._select_screen_status = lambda: {"active": True, "patch_present": True}
        runtime._ROSTER_STATE["extra_characters_requested"] = True
        runtime._ROSTER_STATE["extra_characters_enabled"] = True
        runtime._YAMI_HOVER_DISPLAY_PROFILE_SESSION.clear()

    def tearDown(self) -> None:
        runtime._safe_read_u32be = self.saved["_safe_read_u32be"]
        runtime._safe_write_u32be = self.saved["_safe_write_u32be"]
        runtime._select_screen_status = self.saved["_select_screen_status"]
        runtime._ROSTER_STATE.clear()
        runtime._ROSTER_STATE.update(self.saved["state"])
        runtime._YAMI_HOVER_DISPLAY_PROFILE_SESSION.clear()
        runtime._YAMI_HOVER_DISPLAY_PROFILE_SESSION.update(self.saved["session"])

    def _set_lane(self, lane_index: int, hover: int, focus: int) -> tuple[int, int]:
        _name, hover_addr, focus_addr = runtime.YAMI_HOVER_DISPLAY_PROFILE_LANES[lane_index]
        self.mem.words[int(hover_addr)] = int(hover)
        self.mem.words[int(focus_addr)] = int(focus)
        return int(hover_addr), int(focus_addr)

    def _set_cursor(self, lane_index: int, slot: int) -> int:
        cursor_addr = int(runtime.YAMI_HOVER_DISPLAY_PROFILE_CURSOR_ADDRS[lane_index])
        self.mem.words[cursor_addr] = int(slot)
        return cursor_addr

    def test_yami_has_no_large_card_proxy(self) -> None:
        self.assertEqual(runtime.YAMI_HOVER_DISPLAY_PROFILE_IDS, {})
        hover_addr, focus_addr = self._set_lane(0, 0x17, 0x0C)
        self._set_lane(1, 0x19, 0x1D)

        wrote, failed = runtime._tick_yami_hover_display_profile_route()

        self.assertEqual((wrote, failed), (0, 0))
        self.assertEqual(self.mem.words[hover_addr], 0x17)
        self.assertEqual(self.mem.words[focus_addr], 0x0C)
        self.assertEqual(self.mem.writes, [])

    def test_all_three_yami_ids_resolve_to_no_focus_override(self) -> None:
        for fighter_id in runtime.YAMI_NATIVE_BLANK_IDS:
            desired, detail = runtime._display_profile_for_hover_lane(0, fighter_id)
            self.assertIsNone(desired)
            self.assertEqual(detail, "")

    def test_solo_null_slot_uses_zero_only_at_physical_solo_slot(self) -> None:
        hover_addr, focus_addr = self._set_lane(0, 0x00, 0x1E)
        self._set_cursor(0, runtime.SOLO_NULL_SLOT_INDEX)
        self._set_lane(1, 0x0C, 0x0C)

        self.assertEqual((1, 0), runtime._tick_yami_hover_display_profile_route())
        self.assertEqual(self.mem.words[hover_addr], 0x00)
        self.assertEqual(self.mem.words[focus_addr], runtime.ZERO_VISUAL_PROXY_ID)

    def test_id_zero_outside_the_solo_slot_is_left_alone(self) -> None:
        _hover_addr, focus_addr = self._set_lane(0, 0x00, 0x00)
        self._set_cursor(0, 0x1D)
        self._set_lane(1, 0x0C, 0x0C)

        self.assertEqual((0, 0), runtime._tick_yami_hover_display_profile_route())
        self.assertEqual(self.mem.words[focus_addr], 0x00)
        self.assertEqual(self.mem.writes, [])

    def test_restore_reverts_only_owned_solo_null_cache(self) -> None:
        hover_addr, focus_addr = self._set_lane(0, 0x00, 0x1E)
        self._set_cursor(0, runtime.SOLO_NULL_SLOT_INDEX)
        self._set_lane(1, 0x0C, 0x0C)
        self.assertEqual((1, 0), runtime._tick_yami_hover_display_profile_route())
        self.assertEqual(self.mem.words[focus_addr], runtime.ZERO_VISUAL_PROXY_ID)

        self.assertEqual((1, 0), runtime._restore_yami_hover_display_profile_route_only())
        self.assertEqual(self.mem.words[hover_addr], 0x00)
        self.assertEqual(self.mem.words[focus_addr], 0x1E)


if __name__ == "__main__":
    unittest.main()
