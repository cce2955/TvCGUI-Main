from __future__ import annotations

import struct
import unittest

import tvc_chrsel_yami_cmn_icon as icon


class ChrselYamiCmnIconContractTests(unittest.TestCase):
    def _blob_with_tex0(self, name: bytes = b"icon_cmn\x00") -> tuple[bytes, int]:
        blob = bytearray(0x200)
        header = 0x40
        name_off = 0x160
        blob[header:header + 4] = b"TEX0"
        struct.pack_into(">i", blob, header + 0x14, name_off - header)
        blob[name_off:name_off + len(name)] = name
        return bytes(blob), header

    def test_parse_tex0_name_reads_relative_name_pointer(self) -> None:
        blob, header = self._blob_with_tex0()
        self.assertEqual(icon.parse_tex0_name(blob, 0x92000000, 0x92000000 + header), "icon_cmn")

    def test_find_named_tex0_requires_single_exact_target(self) -> None:
        blob, header = self._blob_with_tex0()
        self.assertEqual(icon.find_named_tex0(blob, 0x92000000), 0x92000000 + header)

    def test_invalid_tex0_is_rejected(self) -> None:
        blob, header = self._blob_with_tex0()
        self.assertIsNone(icon.parse_tex0_name(blob, 0x92000000, 0x92000000 + header + 4))

    def test_patch_list_is_exactly_three_rows_in_two_copies_and_two_layers(self) -> None:
        self.assertEqual(len(icon.PATCH_FIELDS), 12)
        addrs = [addr for _copy, _label, addr in icon.PATCH_FIELDS]
        self.assertEqual(len(addrs), len(set(addrs)))
        bindings = [row for row in icon.PATCH_FIELDS if "binding" in row[1]]
        materials = [row for row in icon.PATCH_FIELDS if "material" in row[1]]
        self.assertEqual(len(bindings), 6)
        self.assertEqual(len(materials), 6)
        self.assertEqual({copy for copy, _label, _addr in icon.PATCH_FIELDS}, {"1015", "1022"})
        self.assertEqual({label.split()[0] for _copy, label, _addr in icon.PATCH_FIELDS}, {"B27", "B28", "B29"})

    def test_1022_b29_material_field_uses_material_record_stride(self) -> None:
        materials_1022 = {label: addr for copy, label, addr in icon.PATCH_FIELDS if copy == "1022" and "material" in label}
        self.assertEqual(materials_1022["B28 material"] - materials_1022["B27 material"], 0x5E0)
        self.assertEqual(materials_1022["B29 material"] - materials_1022["B28 material"], 0x5E0)
        self.assertEqual(materials_1022["B29 material"], 0x932E7FA0)

    def test_cmn_is_the_only_supported_write_target(self) -> None:
        self.assertEqual(icon.TARGET_NAME, b"icon_cmn\x00")
        self.assertNotIn(b"random", icon.TARGET_NAME.lower())


if __name__ == "__main__":
    unittest.main()
