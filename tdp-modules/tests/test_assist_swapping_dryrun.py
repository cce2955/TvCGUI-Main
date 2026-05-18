from __future__ import annotations

import importlib
import struct
import sys
import types
import unittest
from unittest import mock


class AssistSwappingDryRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.write_log: list[tuple[int, bytes]] = []
        self.memory: dict[int, int] = {}

        def rbytes(addr: int, size: int) -> bytes:
            return bytes(self.memory.get(int(addr) + i, 0) for i in range(int(size)))

        def wbytes(addr: int, payload: bytes) -> bool:
            self.write_log.append((int(addr), bytes(payload)))
            for i, b in enumerate(bytes(payload)):
                self.memory[int(addr) + i] = b
            return True

        fake_dolphin_io = types.ModuleType("dolphin_io")
        fake_dolphin_io.rbytes = rbytes
        fake_dolphin_io.wbytes = wbytes

        self.old_dolphin_io = sys.modules.get("dolphin_io")
        sys.modules["dolphin_io"] = fake_dolphin_io
        sys.modules.pop("assist_scanner_backend", None)
        self.assist = importlib.import_module("assist_scanner_backend")
        self.assist._HEADLESS_ASSIST_PROFILES.clear()
        self.assist._HEADLESS_LAST_ASSIST_ATTACK_BY_BASE.clear()
        self.assist._HEADLESS_LAST_PATCH_KEY = None
        self.assist._QUICK_ROUTE_CACHE.clear()

    def tearDown(self) -> None:
        sys.modules.pop("assist_scanner_backend", None)
        if self.old_dolphin_io is not None:
            sys.modules["dolphin_io"] = self.old_dolphin_io
        else:
            sys.modules.pop("dolphin_io", None)

    def _last_payload_for(self, addr: int) -> bytes | None:
        for write_addr, payload in reversed(self.write_log):
            if write_addr == addr:
                return payload
        return None

    def test_ryu_selector_writes_graft_block_and_all_three_lanes(self) -> None:
        row = {"typ": "u32-ryu-selector-graft", "block": 0x90001000, "addr": 0x90001010}

        writes = self.assist._selector_writes_for_row_module(row, 0x0003D620)

        self.assertEqual(self.assist.RYU_SELECTOR_GRAFT_BLOCK, writes[0x90001000])
        for off in self.assist.RYU_SELECTOR_WORD_OFFSETS:
            self.assertEqual(bytes.fromhex("00 03 D6 20"), writes[0x90001000 + off])

    def test_default_ryu_selector_restores_original_graft_block(self) -> None:
        row = {"typ": "u32-ryu-selector-graft", "block": 0x90002000, "addr": 0x90002010}

        writes = self.assist._selector_writes_for_row_module(row, None)

        self.assertEqual({0x90002000: self.assist.RYU_SELECTOR_GRAFT_BLOCK}, writes)

    def test_generic_selector_builds_safe_full_table_payload(self) -> None:
        row = {"typ": "u32-generic-selector", "block": 0x90003000, "addr": 0x90003010, "source": "native"}

        writes = self.assist._selector_writes_for_row_module(row, 0x0003C4AC)
        block = writes[0x90003000]

        self.assertEqual(self.assist.GENERIC_SELECTOR_TABLE_LEN, len(block))
        for off in self.assist.GENERIC_SELECTOR_WORD_OFFSETS:
            self.assertEqual(bytes.fromhex("00 03 C4 AC"), block[off:off + 4])

    def test_write_many_aborts_on_failed_write(self) -> None:
        calls: list[int] = []

        def failing_wbytes(addr: int, payload: bytes) -> bool:
            calls.append(int(addr))
            return int(addr) != 0x90004004

        self.assist.wbytes = failing_wbytes

        ok = self.assist._write_many_module({0x90004000: b"AAAA", 0x90004004: b"BBBB", 0x90004008: b"CCCC"})

        self.assertFalse(ok)
        self.assertEqual([0x90004000, 0x90004004], calls)

    def test_quick_assist_apply_stores_profiles_per_fighter_even_when_character_is_shared(self) -> None:
        owner_by_slot = {"P1-C1": 0xAAAA0000, "P2-C1": 0xBBBB0000}
        row = {"typ": "u32-ryu-selector-graft", "block": 0x90005000, "addr": 0x90005010, "char_id": 12}

        def quicks(slot_label: str, snap: dict | None = None) -> list[dict]:
            if slot_label == "P1-C1":
                return [{"label": "P1 Tatsu H", "raw": "0x11111111"}]
            return [{"label": "P2 Shoryu H", "raw": "0x22222222"}]

        with mock.patch.object(self.assist, "_slot_owner_base_from_label", side_effect=lambda label: owner_by_slot.get(label)), \
             mock.patch.object(self.assist, "get_quick_assists_for_slot", side_effect=quicks), \
             mock.patch.object(self.assist, "_resolve_quick_route_row", return_value=row):
            p1_ok = self.assist.apply_quick_assist_from_main(
                "P1-C1", 0, {"base": 0xF1000000, "char_id": 12, "mv_id_display": 0, "mv_label": "idle"}
            )
            p2_ok = self.assist.apply_quick_assist_from_main(
                "P2-C1", 0, {"base": 0xF2000000, "char_id": 12, "mv_id_display": 0, "mv_label": "idle"}
            )

        self.assertTrue(p1_ok)
        self.assertTrue(p2_ok)
        self.assertEqual(0x11111111, self.assist._HEADLESS_ASSIST_PROFILES[0xF1000000]["word"])
        self.assertEqual(0x22222222, self.assist._HEADLESS_ASSIST_PROFILES[0xF2000000]["word"])

    def test_runtime_assist_attack_patches_the_profile_for_the_calling_fighter(self) -> None:
        owner_by_slot = {"P1-C1": 0xAAAA0000, "P2-C1": 0xBBBB0000}
        shared_row = {"typ": "u32-ryu-selector-graft", "block": 0x90006000, "addr": 0x90006010, "char_id": 12}
        self.assist._HEADLESS_ASSIST_PROFILES[0xF1000000] = {
            "word": 0x11111111,
            "label": "P1 Tatsu H",
            "row": dict(shared_row),
            "fighter_base": 0xF1000000,
            "owner_base": 0xAAAA0000,
            "char_id": 12,
            "is_default": False,
        }
        self.assist._HEADLESS_ASSIST_PROFILES[0xF2000000] = {
            "word": 0x22222222,
            "label": "P2 Shoryu H",
            "row": dict(shared_row),
            "fighter_base": 0xF2000000,
            "owner_base": 0xBBBB0000,
            "char_id": 12,
            "is_default": False,
        }

        with mock.patch.object(self.assist, "_slot_owner_base_from_label", side_effect=lambda label: owner_by_slot.get(label)):
            self.assist._headless_runtime_patch_from_main({
                "P1-C1": {"base": 0xF1000000, "mv_id_display": 0, "mv_label": "idle"},
                "P2-C1": {"base": 0xF2000000, "mv_id_display": 0, "mv_label": "idle"},
            })
            self.write_log.clear()

            self.assist._headless_runtime_patch_from_main({
                "P1-C1": {"base": 0xF1000000, "mv_id_display": 0, "mv_label": "idle"},
                "P2-C1": {"base": 0xF2000000, "mv_id_display": 426, "mv_label": "assist attack"},
            })
            p2_payload = self._last_payload_for(0x90006010)

            self.assist._headless_runtime_patch_from_main({
                "P1-C1": {"base": 0xF1000000, "mv_id_display": 426, "mv_label": "assist attack"},
                "P2-C1": {"base": 0xF2000000, "mv_id_display": 0, "mv_label": "idle"},
            })
            p1_payload = self._last_payload_for(0x90006010)

        self.assertEqual(struct.pack(">I", 0x22222222), p2_payload)
        self.assertEqual(struct.pack(">I", 0x11111111), p1_payload)


if __name__ == "__main__":
    unittest.main()
