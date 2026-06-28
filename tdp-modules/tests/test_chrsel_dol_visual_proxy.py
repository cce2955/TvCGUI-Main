"""Contracts for Character Select's live DOL presentation-tag proxy.

These cover the route that actually works in the current build:

* real selection IDs stay Yami 0x17/0x18/0x19 and solo/null 0x00;
* DOL presentation tags make Yami render through Ryu and solo/null through Zero;
* foreign table values are never overwritten;
* restore affects only values this runtime session still owns.
"""
from __future__ import annotations

import unittest

import tvcgui.features.character_select.runtime as runtime


class _FakeDolphinMemory:
    def __init__(self) -> None:
        self.words: dict[int, int] = {}
        self.bytes: dict[int, int] = {}
        self.writes: list[tuple[int, int]] = []

    def put_bytes(self, addr: int, data: bytes) -> None:
        for offset, value in enumerate(bytes(data)):
            self.bytes[int(addr) + offset] = int(value)

    def read_bytes(self, addr: int, size: int) -> bytes:
        return bytes(self.bytes.get(int(addr) + offset, 0) for offset in range(int(size)))

    def put_word(self, addr: int, value: int) -> None:
        self.words[int(addr)] = int(value) & 0xFFFFFFFF

    def read_word(self, addr: int) -> int | None:
        return self.words.get(int(addr))

    def write_word(self, addr: int, value: int) -> bool:
        self.put_word(addr, value)
        self.writes.append((int(addr), int(value) & 0xFFFFFFFF))
        return True


class CharacterSelectDolVisualProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mem = _FakeDolphinMemory()
        self.saved = {
            "_safe_read": runtime._safe_read,
            "_safe_read_u32be": runtime._safe_read_u32be,
            "_safe_write_u32be": runtime._safe_write_u32be,
            "state": dict(runtime._ROSTER_STATE),
            "session": {
                key: dict(value)
                for key, value in runtime._YAMI_DOL_ICON_TAG_SESSION.items()
            },
        }
        runtime._safe_read = self.mem.read_bytes
        runtime._safe_read_u32be = self.mem.read_word
        runtime._safe_write_u32be = self.mem.write_word
        runtime._clear_yami_dol_icon_tag_session()
        self._seed_stock_route()

    def tearDown(self) -> None:
        runtime._safe_read = self.saved["_safe_read"]
        runtime._safe_read_u32be = self.saved["_safe_read_u32be"]
        runtime._safe_write_u32be = self.saved["_safe_write_u32be"]
        runtime._ROSTER_STATE.clear()
        runtime._ROSTER_STATE.update(self.saved["state"])
        runtime._YAMI_DOL_ICON_TAG_SESSION.clear()
        runtime._YAMI_DOL_ICON_TAG_SESSION.update(self.saved["session"])

    def _seed_stock_route(self) -> None:
        for _proxy_id, (tag_ptr, tag_bytes) in runtime.DOL_TAG_POINTERS.items():
            self.mem.put_bytes(tag_ptr, tag_bytes)
        for base, stock_map in (
            (runtime.DOL_CHAR_TAG_MAP_BASE, runtime._DOL_STOCK_DIRECT_TAG_POINTERS),
            (runtime.DOL_CANONICAL_UI_TAG_MAP_BASE, runtime._DOL_STOCK_CANONICAL_TAG_POINTERS),
        ):
            for fighter_id, tag_ptr in stock_map.items():
                self.mem.put_word(base + int(fighter_id) * 4, tag_ptr)

    @property
    def _expected_addrs(self) -> set[int]:
        return {
            int(base) + int(fighter_id) * 4
            for base in (
                runtime.DOL_CHAR_TAG_MAP_BASE,
                runtime.DOL_CANONICAL_UI_TAG_MAP_BASE,
            )
            for fighter_id, _proxy_id in runtime.CHARSEL_DOL_PRESENTATION_TAG_PLAN
        }

    def test_plan_keeps_playable_ids_and_maps_only_their_visual_profiles(self) -> None:
        self.assertEqual(
            runtime.CHARSEL_DOL_PRESENTATION_TAG_PLAN,
            (
                (0x00, runtime.ZERO_VISUAL_PROXY_ID),
                (0x17, runtime.RYU_VISUAL_PROXY_ID),
                (0x18, runtime.RYU_VISUAL_PROXY_ID),
                (0x19, runtime.RYU_VISUAL_PROXY_ID),
            ),
        )
        self.assertEqual(runtime.RYU_VISUAL_PROXY_ID, 0x0C)
        self.assertEqual(runtime.ZERO_VISUAL_PROXY_ID, 0x1D)

    def test_stock_route_is_ready_and_fresh_before_any_write(self) -> None:
        status = runtime._dol_icon_tag_route_status()
        self.assertTrue(status["ready"])
        self.assertTrue(status["fresh"])
        self.assertFalse(status["installed"])
        self.assertFalse(status["mixed"])
        self.assertEqual(self.mem.writes, [])

    def test_install_writes_exactly_the_eight_presentation_map_words(self) -> None:
        wrote, failed = runtime._install_yami_dol_icon_tag_route()
        self.assertEqual((wrote, failed), (8, 0))
        self.assertEqual({addr for addr, _value in self.mem.writes}, self._expected_addrs)

        ryu_ptr = runtime.DOL_TAG_POINTERS[runtime.RYU_VISUAL_PROXY_ID][0]
        zero_ptr = runtime.DOL_TAG_POINTERS[runtime.ZERO_VISUAL_PROXY_ID][0]
        for base in (
            runtime.DOL_CHAR_TAG_MAP_BASE,
            runtime.DOL_CANONICAL_UI_TAG_MAP_BASE,
        ):
            self.assertEqual(self.mem.read_word(base + 0x00 * 4), zero_ptr)
            for fighter_id in (0x17, 0x18, 0x19):
                self.assertEqual(self.mem.read_word(base + fighter_id * 4), ryu_ptr)

    def test_install_is_idempotent_after_exact_route_is_present(self) -> None:
        self.assertEqual((8, 0), runtime._install_yami_dol_icon_tag_route())
        self.mem.writes.clear()
        self.assertEqual((0, 0), runtime._install_yami_dol_icon_tag_route())
        self.assertEqual(self.mem.writes, [])

    def test_legacy_cmn_ts2_fra_route_is_migrated_to_ryu(self) -> None:
        for base in (
            runtime.DOL_CHAR_TAG_MAP_BASE,
            runtime.DOL_CANONICAL_UI_TAG_MAP_BASE,
        ):
            for fighter_id, tag_ptr in runtime._DOL_LEGACY_PRESENTATION_POINTERS.items():
                self.mem.put_word(base + int(fighter_id) * 4, tag_ptr)

        status = runtime._dol_icon_tag_route_status()
        self.assertTrue(status["ready"])
        self.assertTrue(status["migratable_legacy"])
        self.assertFalse(status["fresh"])
        self.assertEqual((8, 0), runtime._install_yami_dol_icon_tag_route())
        self.assertTrue(runtime._dol_icon_tag_route_status()["installed"])

    def test_foreign_dol_map_value_is_not_overwritten(self) -> None:
        foreign_addr = runtime.DOL_CHAR_TAG_MAP_BASE + 0x18 * 4
        self.mem.put_word(foreign_addr, 0xDEADBEEF)
        status = runtime._dol_icon_tag_route_status()
        self.assertFalse(status["ready"])
        self.assertTrue(status["mixed"])

        wrote, failed = runtime._install_yami_dol_icon_tag_route()
        self.assertEqual((wrote, failed), (0, 1))
        self.assertEqual(self.mem.read_word(foreign_addr), 0xDEADBEEF)
        self.assertEqual(self.mem.writes, [])

    def test_restore_returns_only_values_still_owned_by_this_session(self) -> None:
        self.assertEqual((8, 0), runtime._install_yami_dol_icon_tag_route())
        foreign_addr = runtime.DOL_CANONICAL_UI_TAG_MAP_BASE + 0x19 * 4
        self.mem.put_word(foreign_addr, 0xDEADBEEF)
        self.mem.writes.clear()

        wrote, failed = runtime._restore_yami_dol_icon_tag_route_only()

        self.assertEqual((wrote, failed), (7, 1))
        self.assertEqual(self.mem.read_word(foreign_addr), 0xDEADBEEF)
        self.assertNotIn(foreign_addr, {addr for addr, _value in self.mem.writes})


if __name__ == "__main__":
    unittest.main()
