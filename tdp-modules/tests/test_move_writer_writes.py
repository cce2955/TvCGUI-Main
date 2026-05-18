from __future__ import annotations

import importlib
import struct
import sys
import types
import unittest


class MoveWriterWritesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.byte_writes: list[tuple[int, int]] = []
        self.float_writes: list[tuple[int, float]] = []

        def wd8(addr: int, value: int) -> bool:
            self.byte_writes.append((int(addr), int(value) & 0xFF))
            return True

        def wdf32(addr: int, value: float) -> bool:
            self.float_writes.append((int(addr), float(value)))
            return True

        fake_dolphin_io = types.ModuleType("dolphin_io")
        fake_dolphin_io.wd8 = wd8
        fake_dolphin_io.wdf32 = wdf32

        self.old_dolphin_io = sys.modules.get("dolphin_io")
        sys.modules["dolphin_io"] = fake_dolphin_io
        sys.modules.pop("move_writer", None)
        self.move_writer = importlib.import_module("move_writer")

    def tearDown(self) -> None:
        sys.modules.pop("move_writer", None)
        if self.old_dolphin_io is not None:
            sys.modules["dolphin_io"] = self.old_dolphin_io
        else:
            sys.modules.pop("dolphin_io", None)

    def test_damage_writes_three_big_endian_bytes_at_confirmed_offset(self) -> None:
        ok = self.move_writer.write_damage({"damage_addr": 0x90001000}, 0x123456)

        self.assertTrue(ok)
        self.assertEqual(
            [(0x90001005, 0x12), (0x90001006, 0x34), (0x90001007, 0x56)],
            self.byte_writes,
        )

    def test_active_frames_write_minus_one_and_clamp_end(self) -> None:
        ok = self.move_writer.write_active_frames({"active_addr": 0x90002000}, 6, 4)

        self.assertTrue(ok)
        self.assertEqual([(0x90002008, 5), (0x90002010, 5)], self.byte_writes)

    def test_core_single_byte_fields_use_expected_offsets(self) -> None:
        mv = {
            "meter_addr": 0x90003000,
            "stun_addr": 0x90004000,
            "atkprop_addr": 0x90005000,
        }

        self.assertTrue(self.move_writer.write_meter(mv, 0x123))
        self.assertTrue(self.move_writer.write_hitstun(mv, 0x44))
        self.assertTrue(self.move_writer.write_blockstun(mv, 0x55))
        self.assertTrue(self.move_writer.write_hitstop(mv, 0x66))
        self.assertTrue(self.move_writer.write_attack_property(mv, 0x77))

        self.assertEqual(
            [
                (0x90003018, 0x23),
                (0x9000400F, 0x44),
                (0x9000401F, 0x55),
                (0x90004026, 0x66),
                (0x9000500F, 0x77),
            ],
            self.byte_writes,
        )

    def test_knockback_writes_only_requested_fields(self) -> None:
        ok = self.move_writer.write_knockback({"knockback_addr": 0x90006000}, kb0=1, kb1=None, traj=3)

        self.assertTrue(ok)
        self.assertEqual([(0x90006001, 1), (0x9000600C, 3)], self.byte_writes)

    def test_hitbox_radius_uses_dynamic_offset_then_fallback(self) -> None:
        self.assertTrue(self.move_writer.write_hitbox_radius({"abs": 0x90007000, "hb_off": 0x88}, 12.5))
        self.assertTrue(self.move_writer.write_hitbox_radius({"abs": 0x90008000}, 9.25))

        self.assertEqual([(0x90007088, 12.5), (0x9000821C, 9.25)], self.float_writes)

    def test_missing_addresses_fail_without_writing(self) -> None:
        self.assertFalse(self.move_writer.write_damage({}, 100))
        self.assertFalse(self.move_writer.write_meter({}, 1))
        self.assertFalse(self.move_writer.write_active_frames({}, 1, 2))
        self.assertFalse(self.move_writer.write_attack_property({}, 1))
        self.assertEqual([], self.byte_writes)


if __name__ == "__main__":
    unittest.main()
