from __future__ import annotations

import ast
import unittest
from pathlib import Path

import tvcgui.tools.character_select.icon_tex0_probe as probe


class ChrselIconProbeContractTests(unittest.TestCase):
    def test_probe_is_read_only_by_source_contract(self) -> None:
        source = Path(probe.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in {"dio", "dolphin_io"}
        }
        # Runtime calls go through the lazy adapter, keeping this module testable
        # without Dolphin attached. No Dolphin write API is allowed anywhere.
        self.assertNotIn("wbytes", called)
        self.assertNotIn("wd8", called)
        self.assertNotIn("wd16", called)
        self.assertNotIn("wd32", called)
        self.assertNotIn("write_bytes", called)
        self.assertIn("rbytes", source)
        self.assertIn("hook", source)

    def test_six_binding_destinations_are_unique(self) -> None:
        addrs = [addr for _label, addr in probe.B27_B29_BINDINGS]
        self.assertEqual(6, len(addrs))
        self.assertEqual(len(addrs), len(set(addrs)))

    def test_six_header_destinations_are_unique(self) -> None:
        addrs = [addr for _label, addr in probe.B27_B29_MATERIAL_TEX0]
        self.assertEqual(6, len(addrs))
        self.assertEqual(len(addrs), len(set(addrs)))

    def test_cmn_and_random_are_explicit_capture_targets(self) -> None:
        self.assertIn(b"icon_cmn\x00", probe.TARGET_NAMES)
        self.assertIn(b"icon_random0\x00", probe.TARGET_NAMES)

    def test_window_scan_reports_absolute_name_address_and_word_refs(self) -> None:
        base = 0x92000000
        blob = b"\x00" * 16 + b"icon_cmn\x00" + b"\x00" * 3
        name_addr = base + 16
        blob += name_addr.to_bytes(4, "big") + b"\x00" * 16
        report = probe.scan_resource_window("test", base, blob)
        hit = report["target_names"]["icon_cmn"][0]
        self.assertEqual("0x92000010", hit["addr"])
        self.assertIn("0x9200001C", hit["word_refs"])

    def test_find_u32_refs_requires_aligned_word(self) -> None:
        base = 0x92000000
        target = 0x92001234
        blob = b"X" + target.to_bytes(4, "big") + b"\x00" * 8
        self.assertEqual([], probe.find_u32_refs(blob, base, target))


if __name__ == "__main__":
    unittest.main()
