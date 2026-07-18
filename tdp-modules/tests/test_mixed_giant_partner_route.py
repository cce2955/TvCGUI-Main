"""Contracts for normal + giant two-character team validation."""
from __future__ import annotations

import unittest

import tvcgui.features.character_select.runtime as runtime


class _Memory:
    def __init__(self) -> None:
        self.words: dict[int, int] = {}
        self.writes: list[tuple[int, int]] = []

    def read_word(self, addr: int) -> int | None:
        return self.words.get(int(addr))

    def write_word(self, addr: int, value: int) -> bool:
        value_i = int(value) & 0xFFFFFFFF
        self.words[int(addr)] = value_i
        self.writes.append((int(addr), value_i))
        return True


class MixedGiantPartnerRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mem = _Memory()
        self.now = 100.0
        self.saved = {
            "_select_screen_status": runtime._select_screen_status,
            "_safe_read_u32be": runtime._safe_read_u32be,
            "_safe_write_u32be": runtime._safe_write_u32be,
            "monotonic": runtime.time.monotonic,
            "state": dict(runtime._ROSTER_STATE),
            "deadlines": dict(runtime._MIXED_GIANT_PARTNER_DEADLINES),
        }
        runtime._select_screen_status = lambda: {"active": True}
        runtime._safe_read_u32be = self.mem.read_word
        runtime._safe_write_u32be = self.mem.write_word
        runtime.time.monotonic = lambda: self.now
        runtime._clear_mixed_giant_partner_latches()
        for _lane, _hover, _selected, reject in runtime.MIXED_GIANT_PARTNER_LANES:
            self.mem.words[int(reject)] = 0x000000FF

    def tearDown(self) -> None:
        runtime._select_screen_status = self.saved["_select_screen_status"]
        runtime._safe_read_u32be = self.saved["_safe_read_u32be"]
        runtime._safe_write_u32be = self.saved["_safe_write_u32be"]
        runtime.time.monotonic = self.saved["monotonic"]
        runtime._ROSTER_STATE.clear()
        runtime._ROSTER_STATE.update(self.saved["state"])
        runtime._MIXED_GIANT_PARTNER_DEADLINES.clear()
        runtime._MIXED_GIANT_PARTNER_DEADLINES.update(self.saved["deadlines"])

    def _seed_lane(self, lane: str, *, selected: int, hover: int) -> int:
        for label, hover_addr, selected_addr, reject_addr in runtime.MIXED_GIANT_PARTNER_LANES:
            if label == lane:
                self.mem.words[int(selected_addr)] = int(selected)
                self.mem.words[int(hover_addr)] = int(hover)
                return int(reject_addr)
        raise AssertionError(lane)

    def test_dump_shape_is_a_big_endian_word_not_a_byte(self) -> None:
        reject = self._seed_lane("P1", selected=0x0C, hover=0x0B)
        self.assertEqual(self.mem.words[reject], 0x000000FF)

    def test_ryu_plus_gold_lightan_clears_entire_p1_reject_word(self) -> None:
        reject = self._seed_lane("P1", selected=0x0C, hover=0x0B)
        runtime._tick_mixed_giant_partner_unlock()
        self.assertIn((reject, 0), self.mem.writes)
        self.assertEqual(self.mem.words[reject], 0)
        self.assertTrue(runtime._ROSTER_STATE["mixed_giant_partner_enabled"])
        self.assertIn("Ryu (ID 0x0C) + Gold Lightan (ID 0x0B)", runtime._ROSTER_STATE["mixed_giant_partner_detail"])
        self.assertEqual({addr for addr, _value in self.mem.writes}, {reject})

    def test_zero_plus_ptx_is_accepted_on_p2(self) -> None:
        reject = self._seed_lane("P2", selected=0x1D, hover=0x16)
        runtime._tick_mixed_giant_partner_unlock()
        self.assertEqual(self.mem.words[reject], 0)
        self.assertIn("Zero (ID 0x1D) + PTX-40A (ID 0x16)", runtime._ROSTER_STATE["mixed_giant_partner_detail"])

    def test_normal_plus_normal_does_not_touch_validation(self) -> None:
        self._seed_lane("P1", selected=0x0C, hover=0x1D)
        runtime._tick_mixed_giant_partner_unlock()
        self.assertEqual(self.mem.writes, [])
        self.assertFalse(runtime._ROSTER_STATE["mixed_giant_partner_enabled"])

    def test_commit_latch_reclears_full_word_after_game_rebuild(self) -> None:
        reject = self._seed_lane("P1", selected=0x0C, hover=0x0B)
        runtime._tick_mixed_giant_partner_unlock()
        self.mem.writes.clear()
        self.mem.words[reject] = 0x000000FF
        self.mem.words[0x809BCF1C] = 0x0C
        self.now += 0.5
        runtime._tick_mixed_giant_partner_unlock()
        self.assertEqual(self.mem.writes, [(reject, 0)])
        self.assertIn("commit latch P1", runtime._ROSTER_STATE["mixed_giant_partner_detail"])

    def test_route_stops_outside_character_select(self) -> None:
        self._seed_lane("P1", selected=0x0C, hover=0x0B)
        runtime._select_screen_status = lambda: {"active": False}
        runtime._tick_mixed_giant_partner_unlock()
        self.assertEqual(self.mem.writes, [])
        self.assertFalse(runtime._ROSTER_STATE["mixed_giant_partner_enabled"])


if __name__ == "__main__":
    unittest.main()
