from __future__ import annotations

import unittest

import tvcgui.features.character_select.runtime as runtime


class _VirtualMemory:
    def __init__(self) -> None:
        self.words: dict[int, int] = {}

    def read_u32(self, addr: int) -> int | None:
        return self.words.get(int(addr))

    def write_u32(self, addr: int, value: int) -> bool:
        self.words[int(addr)] = int(value) & 0xFFFFFFFF
        return True


class YamiHoverDisplayProfileTests(unittest.TestCase):
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
        self.mem.words[hover_addr] = hover
        self.mem.words[focus_addr] = focus
        return hover_addr, focus_addr

    def test_requested_profile_ids_replace_only_the_stale_display_cache(self) -> None:
        p1_hover, p1_focus = self._set_lane(0, 0x17, 0x1E)  # dump: Yami 1 -> stale Frank
        p2_hover, p2_focus = self._set_lane(1, 0x18, 0x1E)

        wrote, failed = runtime._tick_yami_hover_display_profile_route()

        self.assertEqual((2, 0), (wrote, failed))
        self.assertEqual(0x17, self.mem.words[p1_hover])  # playable fighter remains Yami 1
        self.assertEqual(0x18, self.mem.words[p2_hover])  # playable fighter remains Yami 2
        self.assertEqual(runtime.RYU_VISUAL_PROXY_ID, self.mem.words[p1_focus])
        self.assertEqual(runtime.RYU_VISUAL_PROXY_ID, self.mem.words[p2_focus])
        self.assertTrue(runtime._ROSTER_STATE["yami_hover_display_profile_installed"])

    def test_yami3_routes_to_ryu_without_touching_hover_id(self) -> None:
        hover_addr, focus_addr = self._set_lane(0, 0x19, 0x1D)
        self._set_lane(1, 0x0C, 0x0C)  # stock hover: untouched

        wrote, failed = runtime._tick_yami_hover_display_profile_route()

        self.assertEqual((1, 0), (wrote, failed))
        self.assertEqual(0x19, self.mem.words[hover_addr])
        self.assertEqual(runtime.RYU_VISUAL_PROXY_ID, self.mem.words[focus_addr])

    def _set_cursor(self, lane_index: int, slot: int) -> int:
        cursor_addr = runtime.YAMI_HOVER_DISPLAY_PROFILE_CURSOR_ADDRS[lane_index]
        self.mem.words[cursor_addr] = slot
        return cursor_addr

    def test_solo_null_slot_uses_zero_profile_without_changing_null_id(self) -> None:
        hover_addr, focus_addr = self._set_lane(0, 0x00, 0x1E)
        self._set_cursor(0, runtime.SOLO_NULL_SLOT_INDEX)
        self._set_lane(1, 0x0C, 0x0C)

        wrote, failed = runtime._tick_yami_hover_display_profile_route()

        self.assertEqual((1, 0), (wrote, failed))
        self.assertEqual(0x00, self.mem.words[hover_addr])  # stays the solo/null partner
        self.assertEqual(runtime.ZERO_VISUAL_PROXY_ID, self.mem.words[focus_addr])
        self.assertTrue(runtime._ROSTER_STATE["yami_hover_display_profile_installed"])

    def test_id_zero_outside_the_solo_slot_is_not_visualized_as_zero(self) -> None:
        _hover_addr, focus_addr = self._set_lane(0, 0x00, 0x00)
        self._set_cursor(0, 0x1D)  # ordinary Ryu row, not appended solo row
        self._set_lane(1, 0x0C, 0x0C)

        wrote, failed = runtime._tick_yami_hover_display_profile_route()

        self.assertEqual((0, 0), (wrote, failed))
        self.assertEqual(0x00, self.mem.words[focus_addr])

    def test_stock_hover_is_not_written_or_owned(self) -> None:
        _hover_addr, focus_addr = self._set_lane(0, 0x1E, 0x1E)
        self._set_lane(1, 0x0C, 0x0C)

        wrote, failed = runtime._tick_yami_hover_display_profile_route()

        self.assertEqual((0, 0), (wrote, failed))
        self.assertEqual(0x1E, self.mem.words[focus_addr])
        self.assertFalse(runtime._YAMI_HOVER_DISPLAY_PROFILE_SESSION)

    def test_restore_only_reverts_a_cache_value_still_owned_while_yami_is_hovered(self) -> None:
        _hover_addr, focus_addr = self._set_lane(0, 0x17, 0x1E)
        self._set_lane(1, 0x0C, 0x0C)
        self.assertEqual((1, 0), runtime._tick_yami_hover_display_profile_route())
        self.assertEqual(runtime.RYU_VISUAL_PROXY_ID, self.mem.words[focus_addr])

        restored, failed = runtime._restore_yami_hover_display_profile_route_only()
        self.assertEqual((1, 0), (restored, failed))
        self.assertEqual(0x1E, self.mem.words[focus_addr])

    def test_restore_never_overwrites_a_stock_update_after_leaving_yami(self) -> None:
        hover_addr, focus_addr = self._set_lane(0, 0x17, 0x1E)
        self._set_lane(1, 0x0C, 0x0C)
        self.assertEqual((1, 0), runtime._tick_yami_hover_display_profile_route())
        self.mem.words[hover_addr] = 0x0C
        self.mem.words[focus_addr] = 0x0C

        restored, failed = runtime._restore_yami_hover_display_profile_route_only()
        self.assertEqual((0, 0), (restored, failed))
        self.assertEqual(0x0C, self.mem.words[focus_addr])


if __name__ == "__main__":
    unittest.main(verbosity=2)
