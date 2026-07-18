"""Contracts for retiring the speculative executable giant classifier patches."""
from __future__ import annotations

import unittest

import tvcgui.features.character_select.runtime as runtime


class _WordMemory:
    def __init__(self) -> None:
        self.words: dict[int, int] = {}
        self.writes: list[tuple[int, int]] = []

    def read(self, addr: int) -> int | None:
        return self.words.get(int(addr))

    def write(self, addr: int, value: int) -> bool:
        value_i = int(value) & 0xFFFFFFFF
        self.words[int(addr)] = value_i
        self.writes.append((int(addr), value_i))
        return True


class MixedGiantEligibilityClassifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mem = _WordMemory()
        self.saved = {
            "read": runtime._safe_read_u32be,
            "write": runtime._safe_write_u32be,
            "state": dict(runtime._ROSTER_STATE),
        }
        runtime._safe_read_u32be = self.mem.read
        runtime._safe_write_u32be = self.mem.write

    def tearDown(self) -> None:
        runtime._safe_read_u32be = self.saved["read"]
        runtime._safe_write_u32be = self.saved["write"]
        runtime._ROSTER_STATE.clear()
        runtime._ROSTER_STATE.update(self.saved["state"])

    def test_native_words_are_left_untouched(self) -> None:
        for addr, expected, _accepted, _label in runtime.GIANT_ELIGIBILITY_PATCHES:
            self.mem.words[int(addr)] = int(expected)
        self.assertEqual(runtime._install_mixed_giant_eligibility_classifier(), (0, 0))
        self.assertEqual(self.mem.writes, [])
        self.assertFalse(runtime._ROSTER_STATE["mixed_giant_classifier_installed"])

    def test_legacy_active_words_are_restored_to_native(self) -> None:
        for addr, _expected, accepted, _label in runtime.GIANT_ELIGIBILITY_PATCHES:
            self.mem.words[int(addr)] = int(accepted)
        wrote, failed = runtime._install_mixed_giant_eligibility_classifier()
        self.assertEqual((wrote, failed), (len(runtime.GIANT_ELIGIBILITY_PATCHES), 0))
        for addr, expected, _accepted, _label in runtime.GIANT_ELIGIBILITY_PATCHES:
            self.assertEqual(self.mem.words[int(addr)], int(expected))

    def test_foreign_code_is_not_overwritten(self) -> None:
        for addr, expected, _accepted, _label in runtime.GIANT_ELIGIBILITY_PATCHES:
            self.mem.words[int(addr)] = int(expected)
        addr, _expected, _accepted, _label = runtime.GIANT_ELIGIBILITY_CODE_PATCHES[0]
        self.mem.words[int(addr)] = 0xDEADBEEF
        wrote, failed = runtime._install_mixed_giant_eligibility_classifier()
        self.assertEqual((wrote, failed), (0, 0))
        self.assertEqual(self.mem.words[int(addr)], 0xDEADBEEF)

    def test_yami_icon_and_large_card_routes_remain_separate(self) -> None:
        self.assertEqual(runtime.YAMI_HOVER_DISPLAY_PROFILE_IDS, {})
        self.assertTrue(
            all(
                source_row == 26 and material_id == 0x001B
                for _label, _target_row, source_row, material_id in runtime.YAMI_HOVER_ICON_ID_PLAN
            )
        )


if __name__ == "__main__":
    unittest.main()
