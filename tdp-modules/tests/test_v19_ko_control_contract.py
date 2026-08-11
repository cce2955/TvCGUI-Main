from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

if "dolphin_memory_engine" not in sys.modules:
    sys.modules["dolphin_memory_engine"] = types.SimpleNamespace(
        is_hooked=lambda: False,
        hook=lambda: None,
        read_bytes=lambda _addr, size: b"\0" * int(size),
        write_bytes=lambda _addr, _data: None,
    )

from tests.v19_contract_helpers import function_source, read, sha256
from tvcgui.runtime import ko_control as ko

MAIN = "main.py"
KO = "tvcgui/runtime/ko_control.py"
KO_DUP = "tdp-modules/tvcgui/runtime/ko_control.py"


class V19KoControlContractTests(unittest.TestCase):
    def test_duplicate_ko_module_matches_primary(self):
        self.assertEqual(sha256(KO), sha256(KO_DUP))

    def test_main_imports_auto_mode(self):
        import_region = read(MAIN)[:12000]
        self.assertIn("apply_ko_control_auto_mode", import_region)
        self.assertIn("tick_ko_control_auto", import_region)

    def test_main_defaults_toggle_off(self):
        self.assertIn("ko_control_full_enabled = False", read(MAIN))

    def test_main_restores_originals_on_startup(self):
        self.assertIn('apply_ko_control_auto_mode("off", verify=False)', read(MAIN))

    def test_main_startup_message_is_direct_off(self):
        self.assertIn("[ko control] default OFF; restored KO/input DOL originals", read(MAIN))

    def test_main_click_flips_boolean(self):
        self.assertIn("ko_control_full_enabled = not bool(ko_control_full_enabled)", read(MAIN))

    def test_main_click_arms_safe_mode(self):
        self.assertIn('apply_ko_control_auto_mode("safe" if ko_control_full_enabled else "off", verify=True)', read(MAIN))

    def test_main_imports_survivor_movement_maintenance(self):
        self.assertIn("maintain_ko_survivor_control", read(MAIN)[:14000])

    def test_main_auto_escalates_only_after_ko(self):
        source = read(MAIN)
        self.assertIn("if bool(ko_control_full_enabled):", source)
        self.assertIn("tick_ko_control_auto(", source)
        self.assertIn("baseline_by_slot=ko_rewind_baseline_by_slot", source)

    def test_main_does_not_force_full_patch_during_live_play(self):
        source = read(MAIN)
        self.assertNotIn("ensure_ko_control_full_patch(True, verify=False)", source)

    def test_safe_packet_excludes_cpu_leaky_raw_pad_route(self):
        safe = dict(ko.KO_CONTROL_SAFE_PACKET)
        full = dict(ko.KO_CONTROL_FULL_PACKET)
        self.assertNotIn(0x80076820, safe)
        self.assertNotIn(0x80076904, safe)
        self.assertIn(0x80076820, full)
        self.assertIn(0x80076904, full)

    def test_native_battle_state_reader_uses_recomp_pointer_chain(self):
        obj = 0x90822E40
        reads = {
            ko.KO_NATIVE_BATTLE_STATE_PTR: obj,
            obj + 0x0C: 9,
        }
        with mock.patch.object(ko, "rd32", side_effect=lambda addr: reads.get(addr)):
            self.assertEqual(ko.read_ko_native_battle_state(), 9)

    def test_auto_tick_promotes_full_from_native_result_state_even_if_snapshots_look_live(self):
        snaps = {
            "P1-C1": {"base": 0x1000, "cur": 100, "attA": 1},
            "P2-C1": {"base": 0x2000, "cur": 100, "attA": 1},
        }
        calls = []
        ko.tick_ko_control_auto.full_until = 0.0
        with mock.patch.object(ko, "read_ko_native_battle_state", return_value=9), \
             mock.patch.object(ko, "rd32", side_effect=lambda addr: 100 if (addr & 0xFFF) == 0x28 else 0), \
             mock.patch.object(ko, "apply_ko_control_auto_mode", side_effect=lambda mode, verify=False: calls.append(mode) or {"ok": True}), \
             mock.patch.object(ko, "_ko_ctrl_release_survivors", return_value={"released": 0, "wrote": 0, "slots": []}):
            active, _last, _result, state = ko.tick_ko_control_auto(True, False, snaps, 20.0, 0.0, verify=False)
        self.assertTrue(active)
        self.assertEqual(state["native_battle_state"], 9)
        self.assertTrue(state["native_result_now"])
        self.assertEqual(state["auto_mode"], "full")
        self.assertIn("full", calls)

    def test_post_ko_latch_survives_native_state_change_until_new_round(self):
        dead_snaps = {
            "P1-C1": {"base": 0x1000, "cur": 100, "attA": 1},
            "P2-C1": {"base": 0x2000, "cur": 0, "attA": 0x9A},
        }
        live_snaps = {
            "P1-C1": {"base": 0x1000, "cur": 100, "attA": 1},
            "P2-C1": {"base": 0x2000, "cur": 100, "attA": 1},
        }
        calls = []
        ko.tick_ko_control_auto.full_until = 0.0
        ko.tick_ko_control_auto.post_ko_latched = False
        with mock.patch.object(ko, "read_ko_native_battle_state", return_value=9), \
             mock.patch.object(ko, "rd32", side_effect=lambda addr: 0 if addr == 0x2000 + 0x28 else (100 if (addr & 0xFFF) == 0x28 else 0)), \
             mock.patch.object(ko, "apply_ko_control_auto_mode", side_effect=lambda mode, verify=False: calls.append(mode) or {"ok": True}), \
             mock.patch.object(ko, "_ko_ctrl_release_survivors", return_value={"released": 0, "wrote": 0, "slots": []}):
            active, last, _result, state = ko.tick_ko_control_auto(True, False, dead_snaps, 20.0, 0.0, verify=False)
        self.assertTrue(active)
        self.assertTrue(state["post_ko_latched"])
        self.assertEqual(state["auto_mode"], "full")

        # A post-KO action may move the native state out of the 9..12 family.
        # The defeated team is still dead, so FULL must remain latched.
        with mock.patch.object(ko, "read_ko_native_battle_state", return_value=5), \
             mock.patch.object(ko, "rd32", side_effect=lambda addr: 0 if addr == 0x2000 + 0x28 else (100 if (addr & 0xFFF) == 0x28 else 0)), \
             mock.patch.object(ko, "apply_ko_control_auto_mode", side_effect=lambda mode, verify=False: calls.append(mode) or {"ok": True}), \
             mock.patch.object(ko, "_ko_ctrl_release_survivors", return_value={"released": 0, "wrote": 0, "slots": []}):
            active, last, _result, state = ko.tick_ko_control_auto(True, active, dead_snaps, 20.2, last, verify=False)
        self.assertTrue(active)
        self.assertTrue(state["post_ko_latched"])
        self.assertEqual(state["auto_mode"], "full")

        # Only a real live round, state 4 with both teams alive, releases FULL.
        with mock.patch.object(ko, "read_ko_native_battle_state", return_value=4), \
             mock.patch.object(ko, "rd32", side_effect=lambda addr: 100 if (addr & 0xFFF) == 0x28 else 0), \
             mock.patch.object(ko, "apply_ko_control_auto_mode", side_effect=lambda mode, verify=False: calls.append(mode) or {"ok": True}):
            active, _last, _result, state = ko.tick_ko_control_auto(True, active, live_snaps, 21.0, last, verify=False)
        self.assertFalse(active)
        self.assertFalse(state["post_ko_latched"])
        self.assertTrue(state["new_live_round"])
        self.assertEqual(state["auto_mode"], "safe")

    def test_native_live_state_drops_back_to_safe_when_both_teams_live(self):
        snaps = {
            "P1-C1": {"base": 0x1000, "cur": 100, "attA": 1},
            "P2-C1": {"base": 0x2000, "cur": 100, "attA": 1},
        }
        calls = []
        ko.tick_ko_control_auto.full_until = 99.0
        ko.tick_ko_control_auto.post_ko_latched = False
        with mock.patch.object(ko, "read_ko_native_battle_state", return_value=4), \
             mock.patch.object(ko, "rd32", side_effect=lambda addr: 100 if (addr & 0xFFF) == 0x28 else 0), \
             mock.patch.object(ko, "apply_ko_control_auto_mode", side_effect=lambda mode, verify=False: calls.append(mode) or {"ok": True}):
            active, _last, _result, state = ko.tick_ko_control_auto(True, True, snaps, 30.0, 0.0, verify=False)
        self.assertFalse(active)
        self.assertEqual(state["native_battle_state"], 4)
        self.assertFalse(state["native_result_now"])
        self.assertEqual(state["auto_mode"], "safe")
        self.assertIn("safe", calls)

    def test_auto_tick_uses_safe_when_both_teams_live(self):
        snaps = {
            "P1-C1": {"base": 0x1000, "cur": 100, "attA": 1},
            "P2-C1": {"base": 0x2000, "cur": 100, "attA": 1},
        }
        calls = []
        ko.tick_ko_control_auto.post_ko_latched = False
        with mock.patch.object(ko, "rd32", side_effect=lambda addr: 100 if (addr & 0xFFF) == 0x28 else 0), \
             mock.patch.object(ko, "apply_ko_control_auto_mode", side_effect=lambda mode, verify=False: calls.append(mode) or {"ok": True}):
            active, _last, _result, state = ko.tick_ko_control_auto(True, True, snaps, 10.0, 0.0, verify=False)
        self.assertFalse(active)
        self.assertEqual(state["auto_mode"], "safe")
        self.assertIn("safe", calls)

    def test_patch_watcher_is_exported(self):
        self.assertTrue(callable(ko.ensure_ko_control_full_patch))
        self.assertGreater(ko.KO_CONTROL_PATCH_WATCH_INTERVAL_SEC, 0.0)

    def test_patch_watcher_repairs_only_mismatches(self):
        expected = dict(ko.KO_CONTROL_FULL_PACKET)
        memory = dict(expected)
        bad_addr = next(iter(expected))
        memory[bad_addr] = expected[bad_addr] ^ 0xFFFFFFFF
        writes = []

        def fake_rd32(addr):
            return memory.get(addr)

        def fake_wd32(addr, value):
            writes.append((addr, value))
            memory[addr] = value
            return True

        with mock.patch.object(ko, "rd32", side_effect=fake_rd32), mock.patch.object(ko, "_ko_wd32", side_effect=fake_wd32):
            result = ko.ensure_ko_control_full_patch(True, verify=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reasserted"], 1)
        self.assertEqual(writes, [(bad_addr, expected[bad_addr])])

    def test_full_packet_is_exact_known_good_length(self):
        self.assertEqual(len(ko.KO_CONTROL_FULL_PACKET), 15)

    def test_original_restore_packet_has_expected_length(self):
        self.assertEqual(len(ko.KO_DOL_ORIGINALS_U32), 78)

    def test_full_packet_keeps_low_input_byte_patch(self):
        self.assertIn((0x80076938, 0x60000000), ko.KO_CONTROL_FULL_PACKET)

    def test_full_packet_skips_result_override(self):
        self.assertIn((0x80048D94, 0x4800001C), ko.KO_CONTROL_FULL_PACKET)

    def test_full_packet_keeps_idle_patch(self):
        self.assertIn((0x80048D9C, 0x38600001), ko.KO_CONTROL_FULL_PACKET)

    def test_full_packet_bypasses_earlier_result_state_pad_gate(self):
        self.assertIn((0x80076820, 0x60000000), ko.KO_CONTROL_FULL_PACKET)
        self.assertIn((0x80076820, 0x408201F8), ko.KO_DOL_ORIGINALS_U32)

    def test_full_packet_forces_pad_read(self):
        self.assertIn((0x80076904, 0x60000000), ko.KO_CONTROL_FULL_PACKET)

    def test_full_packet_forces_buffer_build(self):
        self.assertIn((0x8007637C, 0x48000028), ko.KO_CONTROL_FULL_PACKET)

    def test_full_packet_preserves_result_state_direction_nibble(self):
        self.assertIn((0x80076A80, 0x60000000), ko.KO_CONTROL_FULL_PACKET)
        self.assertIn((0x80076A80, 0x57FF0036), ko.KO_DOL_ORIGINALS_U32)

    def test_full_packet_unlocks_final_result_resolver(self):
        self.assertNotIn((0x800447E0, 0x54600188), ko.KO_CONTROL_FULL_PACKET)
        self.assertNotIn(0x800447E0, dict(ko.KO_CONTROL_FULL_PACKET))
        self.assertIn((0x800447E8, 0x4800000C), ko.KO_CONTROL_FULL_PACKET)

    def test_full_packet_has_no_duplicate_addresses(self):
        addresses = [addr for addr, _value in ko.KO_CONTROL_FULL_PACKET]
        self.assertEqual(len(addresses), len(set(addresses)))

    def test_original_packet_has_no_duplicate_addresses(self):
        addresses = [addr for addr, _value in ko.KO_DOL_ORIGINALS_U32]
        self.assertEqual(len(addresses), len(set(addresses)))

    def test_off_toggle_writes_only_originals(self):
        calls = []

        def fake_write(addr, value, verify=False):
            calls.append((addr, value, verify))
            return 1, 1 if verify else 0, []

        with mock.patch.object(ko, "_write_u32_count", side_effect=fake_write):
            result = ko.apply_ko_control_full_toggle(False, verify=True)
        self.assertEqual([(a, v) for a, v, _ in calls], list(ko.KO_DOL_ORIGINALS_U32))
        self.assertTrue(result["ok"])

    def test_on_toggle_restores_then_applies_full_packet(self):
        calls = []

        def fake_write(addr, value, verify=False):
            calls.append((addr, value, verify))
            return 1, 1 if verify else 0, []

        with mock.patch.object(ko, "_write_u32_count", side_effect=fake_write):
            result = ko.apply_ko_control_full_toggle(True, verify=True)
        expected = list(ko.KO_DOL_ORIGINALS_U32) + list(ko.KO_CONTROL_FULL_PACKET)
        self.assertEqual([(a, v) for a, v, _ in calls], expected)
        self.assertTrue(result["ok"])

    def test_on_toggle_reports_exact_total(self):
        with mock.patch.object(ko, "_write_u32_count", return_value=(1, 0, [])):
            result = ko.apply_ko_control_full_toggle(True, verify=False)
        self.assertEqual(result["total"], len(ko.KO_DOL_ORIGINALS_U32) + len(ko.KO_CONTROL_FULL_PACKET))

    def test_off_toggle_reports_exact_total(self):
        with mock.patch.object(ko, "_write_u32_count", return_value=(1, 0, [])):
            result = ko.apply_ko_control_full_toggle(False, verify=False)
        self.assertEqual(result["total"], len(ko.KO_DOL_ORIGINALS_U32))

    def test_on_toggle_name_is_exact(self):
        with mock.patch.object(ko, "_write_u32_count", return_value=(1, 0, [])):
            result = ko.apply_ko_control_full_toggle(True, verify=False)
        self.assertEqual(result["name"], "KO Control+Full ON")

    def test_off_toggle_name_is_exact(self):
        with mock.patch.object(ko, "_write_u32_count", return_value=(1, 0, [])):
            result = ko.apply_ko_control_full_toggle(False, verify=False)
        self.assertEqual(result["name"], "KO Control+Full OFF")

    def test_toggle_function_has_no_auto_mode_dependency(self):
        source = function_source(KO, "apply_ko_control_full_toggle")
        self.assertNotIn("apply_ko_control_auto_mode", source)
        self.assertNotIn("tick_ko_control_auto", source)

    def test_toggle_failure_is_reported(self):
        with mock.patch.object(ko, "_write_u32_count", return_value=(0, 0, [0x80000000])):
            result = ko.apply_ko_control_full_toggle(True, verify=False)
        self.assertFalse(result["ok"])
        self.assertTrue(result["failed"])


    def test_survivor_release_does_not_rewrite_idle_controls(self):
        source = Path(ko.__file__).read_text(encoding="utf-8")
        release_start = source.index('def _ko_ctrl_release_survivors')
        release_end = source.index('def _ko_ctrl_reset_survivor_release_state', release_start)
        release_source = source[release_start:release_end]
        self.assertNotIn('_ko_ctrl_apply_neutral_controls(slot, base, snap, baseline_entry)', release_source)
        self.assertNotIn('flag_writes =', release_source)
        self.assertNotIn('_ko_ctrl_bridge_survivor_input(slot, base)', release_source)

    def test_neutral_control_snapshot_covers_same_ryu_freeze_fields(self):
        self.assertIn(0x05C, ko.KO_NEUTRAL_CONTROL_OFFSETS_U32)
        self.assertIn(0x060, ko.KO_NEUTRAL_CONTROL_OFFSETS_U32)
        self.assertIn(0x064, ko.KO_NEUTRAL_CONTROL_OFFSETS_U32)
        self.assertIn(0x088, ko.KO_NEUTRAL_CONTROL_OFFSETS_U32)

    def test_neutral_controls_restore_same_character_post_ko_state(self):
        base = 0x9246B9C0
        memory = {
            base + 0x14: 12,
            base + 0x58: 0x00000001,
            base + 0x5C: 0x00000008,
            base + 0x60: 0x0000A001,
            base + 0x64: 0x00800030,
            base + 0x68: 0x00000000,
            base + 0x6C: 0x00000000,
            base + 0x70: 0x00000000,
            base + 0x88: 0x00001005,
        }
        neutral = {
            0x58: 0x00000001,
            0x5C: 0x00000000,
            0x60: 0x0400A001,
            0x64: 0x00000000,
            0x68: 0x00000000,
            0x6C: 0x00000000,
            0x70: 0x00000000,
            0x88: 0x00000000,
        }
        writes = []

        def fake_rd32(addr):
            return memory.get(addr, 0)

        def fake_wd32(addr, value):
            writes.append((addr, value))
            memory[addr] = value
            return True

        baseline = {"neutral_values": neutral, "neutral_char_id": 12}
        with mock.patch.object(ko, "rd32", side_effect=fake_rd32), mock.patch.object(ko, "_ko_wd32", side_effect=fake_wd32):
            result = ko._ko_ctrl_apply_neutral_controls("P1-C1", base, {"char_id": 12}, baseline)

        self.assertTrue(result["ok"])
        self.assertEqual(memory[base + 0x5C], 0x00000000)
        self.assertEqual(memory[base + 0x60], 0x0400A001)
        self.assertEqual(memory[base + 0x64], 0x00000000)
        self.assertEqual(memory[base + 0x88], 0x00000000)
        self.assertIn((base + 0x64, 0x00000000), writes)

    def test_compact_survivor_bridge_direction_masks(self):
        self.assertEqual(ko._compact_token_to_current_mask(0x00), 0x00000800)
        self.assertEqual(ko._compact_token_to_current_mask(0x08), 0x00200808)
        self.assertEqual(ko._compact_token_to_current_mask(0x04), 0x00100804)
        self.assertEqual(ko._compact_token_to_current_mask(0x02), 0x00400802)
        self.assertEqual(ko._compact_token_to_current_mask(0x01), 0x00800801)

    def test_survivor_bridge_rebuilds_neutral_native_packet(self):
        base = 0x92B6BA00
        memory = {
            base + 0x1380: 0x00000008,
            base + 0x13CC: 0x00000800,
        }
        writes = []

        def fake_rd32(addr):
            return memory.get(addr, 0)

        def fake_wd32(addr, value):
            writes.append((addr, value))
            memory[addr] = value
            return True

        ko._KO_SURVIVOR_INPUT_PREV.clear()
        ko._KO_SURVIVOR_INPUT_LOGGED.clear()
        with mock.patch.object(ko, "rd32", side_effect=fake_rd32), mock.patch.object(ko, "_ko_wd32", side_effect=fake_wd32):
            result = ko._ko_ctrl_bridge_survivor_input("P1-C2", base)

        self.assertEqual(result["bridged"], 1)
        self.assertEqual(memory[base + 0x13CC], 0x00200808)
        self.assertEqual(memory[base + 0x13D8], 0x00000008)
        self.assertIn((base + 0x13C8, 0x00000800), writes)

    def test_character_neutral_baseline_restores_exact_learned_values(self):
        base = 0x92B6BA00
        learned = {
            0x58: 0x00000001,
            0x60: 0x00002001,
            0x64: 0x00000000,
            0x1E8: 0x00000001,
            0x1EC: 0x00000001,
            0x1F4: 0x000001BF,
            0x204: 0x00000002,
            0x214: 0x00000102,
            0x21C: 0x000000C5,
            0x220: 0x000000C5,
        }
        baseline = {"neutral_values": learned, "neutral_char_id": 27}
        memory = {base + 0x14: 27}
        writes = []

        def fake_rd32(addr):
            return memory.get(addr, 0)

        def fake_wd32(addr, value):
            writes.append((addr, value))
            memory[addr] = value
            return True

        with mock.patch.object(ko, "rd32", side_effect=fake_rd32), mock.patch.object(ko, "_ko_wd32", side_effect=fake_wd32):
            result = ko._ko_ctrl_apply_neutral_baseline("P1-C2", base, {"char_id": 27}, baseline)

        self.assertTrue(result["ok"])
        self.assertIn((base + 0x21C, 0x000000C5), writes)
        self.assertNotIn((base + 0x21C, 0x00000036), writes)

    def test_character_neutral_baseline_rejects_stale_character(self):
        base = 0x92B6BA00
        baseline = {"neutral_values": {0x1E8: 1}, "neutral_char_id": 12}
        with mock.patch.object(ko, "rd32", return_value=27):
            result = ko._ko_ctrl_apply_neutral_baseline("P1-C2", base, {"char_id": 27}, baseline)
        self.assertFalse(result["ok"])
        self.assertEqual(result["mode"], "char-mismatch")


if __name__ == "__main__":
    unittest.main()
