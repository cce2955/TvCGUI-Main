from __future__ import annotations

import importlib
import sys
import types
import unittest


class MissionManagerMeterRefillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.memory = {0x10: 7, 0x11: 9}
        self.writes: list[tuple[int, int]] = []

        def rd8(addr: int) -> int | None:
            return self.memory.get(addr)

        def wd8(addr: int, value: int) -> bool:
            value = max(0, min(255, int(value)))
            self.memory[addr] = value
            self.writes.append((addr, value))
            return True

        fake_dolphin_io = types.ModuleType("dolphin_io")
        fake_dolphin_io.rd8 = rd8
        fake_dolphin_io.wd8 = wd8

        self.old_dolphin_io = sys.modules.get("dolphin_io")
        sys.modules["dolphin_io"] = fake_dolphin_io
        sys.modules.pop("mission_manager", None)
        self.mission_manager = importlib.import_module("mission_manager")

    def tearDown(self) -> None:
        sys.modules.pop("mission_manager", None)
        if self.old_dolphin_io is not None:
            sys.modules["dolphin_io"] = self.old_dolphin_io
        else:
            sys.modules.pop("dolphin_io", None)

    def _new_manager(self):
        def read_debug_flags():
            return [
                ("P1Meter", 0x10, self.memory.get(0x10)),
                ("BaroquePct", 0x11, self.memory.get(0x11)),
            ]

        return self.mission_manager.MissionManager(
            move_map={},
            global_map={},
            debug_flag_addrs={},
            read_debug_flags_fn=read_debug_flags,
            move_label_for_fn=lambda anim_id, csv_char_id, move_map, global_map: None,
        )

    def _payload(self, mission_id: str = "alex_017") -> dict:
        return {
            "active": True,
            "slot": "P1-C1",
            "character": "Alex",
            "active_mission_id": mission_id,
            "active_mission_steps": [{"labels": ["5A"]}],
            "active_mission_goal": {},
        }

    def _snaps(self, *, meter: int = 0, opponent_state: int = 0, opponent_hp: int = 200000) -> dict:
        return {
            "P1-C1": {
                "name": "Alex",
                "teamtag": "P1",
                "base": 0x1000,
                "cur": 200000,
                "meter": meter,
                "mv_label": "idle",
                "mv_id_display": 0,
                "inputs": {},
                "attA": 0,
                "attB": 0,
            },
            "P2-C1": {
                "name": "Ryu",
                "teamtag": "P2",
                "base": 0x2000,
                "cur": opponent_hp,
                "meter": 0,
                "mv_label": "idle",
                "mv_id_display": 0,
                "inputs": {},
                "attA": opponent_state,
                "attB": 0,
            },
        }

    def test_refill_missions_force_baroque_and_refill_when_combo_is_not_active(self) -> None:
        mgr = self._new_manager()
        mgr._frame_idx = 1
        mgr._render_snap_by_slot = {}

        mgr._augment_payload_with_runtime(self._payload("alex_017"), self._snaps(meter=0, opponent_state=0))

        self.assertIn((0x11, 1), self.writes)
        self.assertIn((0x10, 1), self.writes)
        self.assertEqual(1, self.memory[0x10])
        self.assertEqual(1, self.memory[0x11])
        self.assertEqual(7, mgr._runtime["saved_p1meter_flag"])
        self.assertEqual(9, mgr._runtime["saved_baroque_flag"])
        self.assertEqual("alex_017", mgr._runtime["saved_meter_flag_mission"])

    def test_refill_missions_disable_free_meter_while_combo_is_active(self) -> None:
        mgr = self._new_manager()
        mgr._frame_idx = 1
        mgr._render_snap_by_slot = {}

        mgr._augment_payload_with_runtime(self._payload("saki_009"), self._snaps(meter=0, opponent_state=48))

        self.assertIn((0x11, 1), self.writes)
        self.assertIn((0x10, 0), self.writes)
        self.assertEqual(0, self.memory[0x10])
        self.assertEqual(1, self.memory[0x11])

    def test_refill_missions_turn_meter_override_off_when_already_full(self) -> None:
        mgr = self._new_manager()
        mgr._frame_idx = 1
        mgr._render_snap_by_slot = {}

        mgr._augment_payload_with_runtime(self._payload("ryu_008"), self._snaps(meter=50000, opponent_state=0))

        self.assertIn((0x10, 0), self.writes)
        self.assertEqual(0, self.memory[0x10])

    def test_non_refill_mission_does_not_touch_meter_flags(self) -> None:
        mgr = self._new_manager()
        mgr._frame_idx = 1
        mgr._render_snap_by_slot = {}

        mgr._augment_payload_with_runtime(self._payload("alex_001"), self._snaps(meter=0, opponent_state=0))

        self.assertNotIn((0x10, 1), self.writes)
        self.assertNotIn((0x10, 0), self.writes)
        self.assertNotIn((0x11, 1), self.writes)
        self.assertEqual(7, self.memory[0x10])
        self.assertEqual(9, self.memory[0x11])

    def test_saved_meter_flags_restore_when_mission_mode_deactivates(self) -> None:
        mgr = self._new_manager()
        mgr._frame_idx = 1
        mgr._render_snap_by_slot = {}
        mgr._active_slot = "P1-C1"

        mgr._augment_payload_with_runtime(self._payload("alex_017"), self._snaps(meter=0, opponent_state=0))
        self.assertEqual(1, self.memory[0x10])
        self.assertEqual(1, self.memory[0x11])

        mgr._active_slot = None
        mgr.write_mode_state()

        self.assertEqual(7, self.memory[0x10])
        self.assertEqual(9, self.memory[0x11])


if __name__ == "__main__":
    unittest.main()
