from __future__ import annotations

import unittest
from unittest import mock

from tests.v19_contract_helpers import function_source, read, sha256
from tvcgui.runtime import ko_control as ko

MAIN = "main.py"
KO = "tvcgui/runtime/ko_control.py"
KO_DUP = "tdp-modules/tvcgui/runtime/ko_control.py"


class V19KoControlContractTests(unittest.TestCase):
    def test_duplicate_ko_module_matches_primary(self):
        self.assertEqual(sha256(KO), sha256(KO_DUP))

    def test_main_imports_direct_full_toggle(self):
        self.assertIn("apply_ko_control_full_toggle", read(MAIN))

    def test_main_does_not_import_auto_tick(self):
        import_region = read(MAIN)[:12000]
        self.assertNotIn("tick_ko_control_auto", import_region)

    def test_main_defaults_toggle_off(self):
        self.assertIn("ko_control_full_enabled = False", read(MAIN))

    def test_main_restores_originals_on_startup(self):
        self.assertIn("apply_ko_control_full_toggle(False, verify=False)", read(MAIN))

    def test_main_startup_message_is_direct_off(self):
        self.assertIn("[ko control] default OFF; restored KO/input DOL originals", read(MAIN))

    def test_main_click_flips_boolean(self):
        self.assertIn("ko_control_full_enabled = not bool(ko_control_full_enabled)", read(MAIN))

    def test_main_click_calls_direct_toggle(self):
        self.assertIn("apply_ko_control_full_toggle(ko_control_full_enabled, verify=True)", read(MAIN))

    def test_main_has_no_auto_mode_call(self):
        self.assertNotIn("apply_ko_control_auto_mode(", read(MAIN))

    def test_full_packet_is_exact_known_good_length(self):
        self.assertEqual(len(ko.KO_CONTROL_FULL_PACKET), 12)

    def test_original_restore_packet_has_expected_length(self):
        self.assertEqual(len(ko.KO_DOL_ORIGINALS_U32), 76)

    def test_full_packet_keeps_low_input_byte_patch(self):
        self.assertIn((0x80076938, 0x60000000), ko.KO_CONTROL_FULL_PACKET)

    def test_full_packet_skips_result_override(self):
        self.assertIn((0x80048D94, 0x4800001C), ko.KO_CONTROL_FULL_PACKET)

    def test_full_packet_keeps_idle_patch(self):
        self.assertIn((0x80048D9C, 0x38600001), ko.KO_CONTROL_FULL_PACKET)

    def test_full_packet_forces_pad_read(self):
        self.assertIn((0x80076904, 0x60000000), ko.KO_CONTROL_FULL_PACKET)

    def test_full_packet_forces_buffer_build(self):
        self.assertIn((0x8007637C, 0x48000028), ko.KO_CONTROL_FULL_PACKET)

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


if __name__ == "__main__":
    unittest.main()
