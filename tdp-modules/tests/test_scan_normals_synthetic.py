from __future__ import annotations

import importlib
import struct
import sys
import types
import unittest
from unittest import mock


class ScanNormalsSyntheticTests(unittest.TestCase):
    def setUp(self) -> None:
        fake_dolphin_io = types.ModuleType("dolphin_io")
        fake_dolphin_io.hook = lambda: None
        fake_dolphin_io.rbytes = lambda addr, size: b""
        fake_dolphin_io.rd32 = lambda addr: 0

        self.old_dolphin_io = sys.modules.get("dolphin_io")
        sys.modules["dolphin_io"] = fake_dolphin_io
        sys.modules.pop("scan_normals_all", None)
        self.scan = importlib.import_module("scan_normals_all")

    def tearDown(self) -> None:
        sys.modules.pop("scan_normals_all", None)
        if self.old_dolphin_io is not None:
            sys.modules["dolphin_io"] = self.old_dolphin_io
        else:
            sys.modules.pop("dolphin_io", None)

    def _chr_tbl_buffer(self, *, act_rel: int = 0xB04, entry0: int = 0x3600) -> tuple[bytearray, int, int]:
        mem_base = 0x90800000
        chr_tbl_abs = mem_base + 0x100
        buf = bytearray(b"\x00" * (0x100 + act_rel + 0x40))
        tbl_off = 0x100
        buf[tbl_off - 0x18:tbl_off - 0x10] = b"chr_tbl\n"
        entries = [entry0, 0x3604, 0, 0x3604, 0x00090004]
        for i, value in enumerate(entries):
            struct.pack_into(">I", buf, tbl_off + i * 4, value)
        struct.pack_into(">I", buf, tbl_off + act_rel - 4, 0xFFFFFFFF)
        buf[tbl_off + act_rel:tbl_off + act_rel + 8] = b"chr_act\n"
        return buf, mem_base, chr_tbl_abs

    def test_validate_and_parse_chr_tbl_accepts_standard_shape_and_dedupes_entries(self) -> None:
        buf, mem_base, chr_tbl_abs = self._chr_tbl_buffer()

        self.assertTrue(self.scan.validate_chr_tbl(bytes(buf), mem_base, chr_tbl_abs))
        moves = self.scan.parse_chr_tbl(bytes(buf), mem_base, chr_tbl_abs)

        self.assertEqual([chr_tbl_abs + 0x3600, chr_tbl_abs + 0x3604], moves)

    def test_validate_chr_tbl_accepts_flexible_long_table_shape(self) -> None:
        buf, mem_base, chr_tbl_abs = self._chr_tbl_buffer(act_rel=0x14C4)

        self.assertTrue(self.scan.validate_chr_tbl(bytes(buf), mem_base, chr_tbl_abs))
        start, end = self.scan.slot_scan_region_from_tbl(bytes(buf), mem_base, chr_tbl_abs)

        self.assertEqual(chr_tbl_abs, start)
        self.assertEqual(chr_tbl_abs + 0x3604 + self.scan.SLOT_REGION_PAD, end)

    def test_resolve_chr_tbl_prefers_fighter_pointer_offset(self) -> None:
        mem_base = 0x9246B9C0
        fighter_base = mem_base
        chr_tbl_abs = 0x90896640
        buf = bytearray(b"\x00" * 0x300)
        struct.pack_into(">I", buf, self.scan.CHR_TBL_PTR_OFF, chr_tbl_abs)
        struct.pack_into(">I", buf, 0x20, 0x90999999)

        self.assertEqual(chr_tbl_abs, self.scan.resolve_chr_tbl(bytes(buf), mem_base, fighter_base))

    def test_field_attachment_does_not_promote_unknown_010e_to_fake_6b(self) -> None:
        moves = [{"kind": "normal", "abs": 0x90801000, "id": 0x010E, "source": "table"}]
        blocks = {
            "meters": [],
            "active_blocks": [],
            "inline_active_blocks": [],
            "dmg_blocks": [],
            "atkprop_blocks": [],
            "hitreact_blocks": [],
            "kb_blocks": [],
            "stun_blocks": [],
        }

        with mock.patch.object(self.scan, "lookup_move_name", return_value=None):
            self.scan.attach_move_fields(moves, b"\x00" * 0x200, 0x90801000, blocks, char_id=12)
        collapsed = self.scan.collapse_duplicate_normals_by_quality(moves)

        self.assertEqual("anim_010E", moves[0]["move_name"])
        self.assertFalse(moves[0]["normal_confirmed"])
        self.assertEqual("special", collapsed[0]["kind"])

    def test_confirmed_6b_from_move_map_can_occupy_normal_slot(self) -> None:
        moves = [{"kind": "normal", "abs": 0x90801000, "id": 0x010E, "source": "table"}]
        blocks = {
            "meters": [],
            "active_blocks": [],
            "inline_active_blocks": [],
            "dmg_blocks": [],
            "atkprop_blocks": [],
            "hitreact_blocks": [],
            "kb_blocks": [],
            "stun_blocks": [],
        }

        with mock.patch.object(self.scan, "lookup_move_name", return_value="6B"):
            self.scan.attach_move_fields(moves, b"\x00" * 0x200, 0x90801000, blocks, char_id=8)
        collapsed = self.scan.collapse_duplicate_normals_by_quality(moves)

        self.assertTrue(moves[0]["normal_confirmed"])
        self.assertEqual("normal", collapsed[0]["kind"])

    def test_duplicate_normals_keep_best_populated_record(self) -> None:
        empty_parent = {"kind": "normal", "abs": 0x1000, "id": 0x0103, "source": "table", "normal_confirmed": True}
        populated_child = {
            "kind": "normal",
            "abs": 0x1200,
            "id": 0x0103,
            "source": "strict",
            "normal_confirmed": True,
            "damage": 600,
            "active_start": 6,
            "active_end": 7,
            "hitstun": 20,
            "kb0": 1,
            "meter_addr": 0x3000,
        }

        collapsed = self.scan.collapse_duplicate_normals_by_quality([empty_parent, populated_child])

        normals = [mv for mv in collapsed if mv.get("kind") == "normal"]
        self.assertEqual(1, len(normals))
        self.assertEqual(0x1200, normals[0]["abs"])


if __name__ == "__main__":
    unittest.main()
