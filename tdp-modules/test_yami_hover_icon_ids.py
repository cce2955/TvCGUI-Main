from __future__ import annotations

import unittest

import tvcgui.features.character_select.runtime as runtime


class _VirtualSeqMemory:
    def __init__(self) -> None:
        self.bytes: dict[int, int] = {}
        self.originals: dict[int, int] = {}
        for _bank_label, bank_base in runtime.YAMI_HOVER_ICON_ROW_BANKS:
            for _label, target_row, source_row, source_value in runtime.YAMI_HOVER_ICON_ID_PLAN:
                source_addr = runtime._yami_hover_icon_field_addr(bank_base, source_row)
                target_addr = runtime._yami_hover_icon_field_addr(bank_base, target_row)
                self.put_u16(source_addr, source_value)
                self.put_u16(target_addr, runtime.YAMI_HOVER_ICON_STOCK_TARGET_IDS[target_row])
                self.originals[target_addr] = runtime.YAMI_HOVER_ICON_STOCK_TARGET_IDS[target_row]

    def put_u16(self, addr: int, value: int) -> None:
        self.bytes[int(addr)] = (int(value) >> 8) & 0xFF
        self.bytes[int(addr) + 1] = int(value) & 0xFF

    def read(self, addr: int, size: int) -> bytes:
        return bytes(self.bytes.get(int(addr) + offset, 0) for offset in range(int(size)))

    def write(self, addr: int, data: bytes) -> bool:
        for offset, value in enumerate(bytes(data)):
            self.bytes[int(addr) + offset] = value
        return True

    def read_u16(self, addr: int) -> int:
        return int.from_bytes(self.read(addr, 2), "big")


class YamiHoverIconIdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mem = _VirtualSeqMemory()
        self.saved = {
            "_safe_read": runtime._safe_read,
            "_safe_write_bytes": runtime._safe_write_bytes,
            "_chrsel_seq_heap_present": runtime._chrsel_seq_heap_present,
            "state": dict(runtime._ROSTER_STATE),
            "session": {key: dict(value) for key, value in runtime._YAMI_HOVER_ICON_ID_SESSION.items()},
        }
        runtime._safe_read = self.mem.read
        runtime._safe_write_bytes = self.mem.write
        runtime._chrsel_seq_heap_present = lambda: True
        runtime._clear_yami_hover_icon_id_session()

    def tearDown(self) -> None:
        runtime._safe_read = self.saved["_safe_read"]
        runtime._safe_write_bytes = self.saved["_safe_write_bytes"]
        runtime._chrsel_seq_heap_present = self.saved["_chrsel_seq_heap_present"]
        runtime._ROSTER_STATE.clear()
        runtime._ROSTER_STATE.update(self.saved["state"])
        runtime._YAMI_HOVER_ICON_ID_SESSION.clear()
        runtime._YAMI_HOVER_ICON_ID_SESSION.update(self.saved["session"])

    def test_each_yami_tile_uses_the_requested_source_icon_id(self) -> None:
        wrote, failed = runtime._install_yami_hover_icon_id_route()
        self.assertEqual((6, 0), (wrote, failed))
        self.assertTrue(runtime._yami_hover_icon_id_route_status()["installed"])

        expected = {27: 0x0001, 28: 0x000A, 29: 0x0024}
        for _bank_label, bank_base in runtime.YAMI_HOVER_ICON_ROW_BANKS:
            for _label, target_row, _source_row, _source_value in runtime.YAMI_HOVER_ICON_ID_PLAN:
                addr = runtime._yami_hover_icon_field_addr(bank_base, target_row)
                self.assertEqual(expected[target_row], self.mem.read_u16(addr))

    def test_restore_returns_all_six_fields_to_their_exact_stock_material_ids(self) -> None:
        self.assertEqual((6, 0), runtime._install_yami_hover_icon_id_route())
        self.assertEqual((6, 0), runtime._restore_yami_hover_icon_id_route_only())
        for addr, original in self.mem.originals.items():
            self.assertEqual(original, self.mem.read_u16(addr))

    def test_mixed_destination_refuses_to_layer_a_write(self) -> None:
        _bank_label, bank_base = runtime.YAMI_HOVER_ICON_ROW_BANKS[0]
        addr = runtime._yami_hover_icon_field_addr(bank_base, 27)
        self.mem.put_u16(addr, 0x7777)
        wrote, failed = runtime._install_yami_hover_icon_id_route()
        self.assertEqual((0, 1), (wrote, failed))
        self.assertEqual(0x7777, self.mem.read_u16(addr))


if __name__ == "__main__":
    unittest.main(verbosity=2)
