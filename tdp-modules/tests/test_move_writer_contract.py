from __future__ import annotations
import sys
import types
import unittest
from unittest.mock import patch

# Unit tests exercise move_writer's packet contract without requiring a live
# Dolphin install. dolphin_io imports this provider at module import time.
if "dolphin_memory_engine" not in sys.modules:
    sys.modules["dolphin_memory_engine"] = types.SimpleNamespace(
        is_hooked=lambda: False,
        hook=lambda: None,
        read_bytes=lambda _addr, size: b"\0" * int(size),
        write_bytes=lambda _addr, _data: None,
    )

import move_writer as mw


DAMAGE_HEADER = bytes((0x35, 0x10, 0x20, 0x3F, 0x00))
ACTIVE_HEADER = bytes((0x20, 0x35, 0x01, 0x20, 0x3F))
STUN_HEADER = bytes((0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x54, 0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00))


class MoveWriterContractTests(unittest.TestCase):
    def setUp(self):
        self.writes = []

    def _wd8(self, addr, value):
        self.writes.append((addr, value))
        return True

    def test_damage_writes_exact_three_bytes_at_packet_plus_five(self):
        mv = {"damage_addr": 0x9000}
        with patch.object(mw, "rbytes", return_value=DAMAGE_HEADER + b"\0" * 20), patch.object(mw, "wd8", self._wd8):
            self.assertTrue(mw.write_damage(mv, 0x123456))
        self.assertEqual(self.writes, [(0x9005, 0x12), (0x9006, 0x34), (0x9007, 0x56)])

    def test_damage_uses_explicit_value_address_when_present(self):
        mv = {"damage_addr": 0x9000, "damage_value_addr": 0x9011}
        with patch.object(mw, "rbytes", return_value=DAMAGE_HEADER + b"\0" * 20), patch.object(mw, "wd8", self._wd8):
            self.assertTrue(mw.write_damage(mv, 9))
        self.assertEqual(self.writes, [(0x9011, 0), (0x9012, 0), (0x9013, 9)])

    def test_damage_rejects_stale_packet_header_without_write(self):
        with patch.object(mw, "rbytes", return_value=b"\0" * 32), patch.object(mw, "wd8", self._wd8):
            self.assertFalse(mw.write_damage({"damage_addr": 0x9000}, 99))
        self.assertEqual(self.writes, [])

    def test_damage_rejects_unverified_target_without_write(self):
        with patch.object(mw, "rbytes", return_value=DAMAGE_HEADER + b"\0" * 20), patch.object(mw, "wd8", self._wd8):
            self.assertFalse(mw.write_damage({"damage_addr": 0x9000, "damage_write_verified": False}, 99))
        self.assertEqual(self.writes, [])

    def test_damage_requires_address(self):
        self.assertFalse(mw.write_damage({}, 1))

    def test_active_clamps_end_to_start(self):
        mv = {"active_addr": 0xA000}
        with patch.object(mw, "rbytes", return_value=ACTIVE_HEADER + b"\0" * 24), patch.object(mw, "wd8", self._wd8):
            self.assertTrue(mw.write_active_frames(mv, 5, 2))
        self.assertEqual(self.writes, [(0xA008, 4), (0xA010, 4)])

    def test_active_rejects_wrong_header(self):
        with patch.object(mw, "rbytes", return_value=b"\x20" * 24), patch.object(mw, "wd8", self._wd8):
            self.assertFalse(mw.write_active_frames({"active_addr": 0xA000}, 1, 2))
        self.assertEqual(self.writes, [])

    def test_hitstun_writes_packet_plus_fifteen(self):
        with patch.object(mw, "rbytes", return_value=STUN_HEADER + b"\0" * 40), patch.object(mw, "wd8", self._wd8):
            self.assertTrue(mw.write_hitstun({"stun_addr": 0xB000}, 42))
        self.assertEqual(self.writes, [(0xB00F, 42)])

    def test_blockstun_writes_packet_plus_thirty_one(self):
        with patch.object(mw, "rbytes", return_value=STUN_HEADER + b"\0" * 40), patch.object(mw, "wd8", self._wd8):
            self.assertTrue(mw.write_blockstun({"stun_addr": 0xB000}, 43))
        self.assertEqual(self.writes, [(0xB01F, 43)])

    def test_hitstop_writes_packet_plus_thirty_eight(self):
        with patch.object(mw, "rbytes", return_value=STUN_HEADER + b"\0" * 40), patch.object(mw, "wd8", self._wd8):
            self.assertTrue(mw.write_hitstop({"stun_addr": 0xB000}, 44))
        self.assertEqual(self.writes, [(0xB026, 44)])

    def test_stun_fields_reject_unverified_target(self):
        for writer in (mw.write_hitstun, mw.write_blockstun, mw.write_hitstop):
            with self.subTest(writer=writer.__name__):
                with patch.object(mw, "rbytes", return_value=STUN_HEADER + b"\0" * 40), patch.object(mw, "wd8", self._wd8):
                    self.assertFalse(writer({"stun_addr": 0xB000, "stun_write_verified": False}, 9))
        self.assertEqual(self.writes, [])

    def test_meter_writes_direct_address(self):
        with patch.object(mw, "wd8", self._wd8):
            self.assertTrue(mw.write_meter({"meter_addr": 0xC000}, 0x1FE))
        self.assertEqual(self.writes, [(0xC000, 0xFE)])

    def test_damage_masks_to_twenty_four_bits(self):
        with patch.object(mw, "rbytes", return_value=DAMAGE_HEADER + b"\0" * 20), patch.object(mw, "wd8", self._wd8):
            self.assertTrue(mw.write_damage({"damage_addr": 0x9000}, 0x12345678))
        self.assertEqual(self.writes, [(0x9005, 0x34), (0x9006, 0x56), (0x9007, 0x78)])

    def test_transport_without_rbytes_preserves_legacy_writer_compatibility(self):
        with patch.object(mw, "rbytes", side_effect=RuntimeError("no read transport")), patch.object(mw, "wd8", self._wd8):
            self.assertTrue(mw.write_damage({"damage_addr": 0x9000}, 1))
        self.assertEqual(self.writes[-1], (0x9007, 1))
