"""Contracts for Yami's small-icon-only Ryu route."""
from __future__ import annotations

import unittest

import tvcgui.features.character_select.runtime as runtime


class _IconMemory:
    def __init__(self) -> None:
        self.halfwords: dict[int, int] = {}
        self.writes: list[tuple[int, bytes]] = []

    def read_u16(self, addr: int) -> int | None:
        return self.halfwords.get(int(addr))

    def write_bytes(self, addr: int, data: bytes) -> bool:
        payload = bytes(data)
        self.writes.append((int(addr), payload))
        if len(payload) == 2:
            self.halfwords[int(addr)] = int.from_bytes(payload, "big")
        return True


class CharacterSelectYamiIconOnlyRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mem = _IconMemory()
        self.saved = {
            "_safe_read_u16be": runtime._safe_read_u16be,
            "_safe_write_bytes": runtime._safe_write_bytes,
            "_chrsel_seq_heap_present": runtime._chrsel_seq_heap_present,
            "state": dict(runtime._ROSTER_STATE),
            "session": {key: dict(value) for key, value in runtime._YAMI_HOVER_ICON_ID_SESSION.items()},
        }
        runtime._safe_read_u16be = self.mem.read_u16
        runtime._safe_write_bytes = self.mem.write_bytes
        runtime._chrsel_seq_heap_present = lambda: True
        runtime._clear_yami_hover_icon_id_session()
        self._seed_stock_rows()

    def tearDown(self) -> None:
        runtime._safe_read_u16be = self.saved["_safe_read_u16be"]
        runtime._safe_write_bytes = self.saved["_safe_write_bytes"]
        runtime._chrsel_seq_heap_present = self.saved["_chrsel_seq_heap_present"]
        runtime._ROSTER_STATE.clear()
        runtime._ROSTER_STATE.update(self.saved["state"])
        runtime._YAMI_HOVER_ICON_ID_SESSION.clear()
        runtime._YAMI_HOVER_ICON_ID_SESSION.update(self.saved["session"])

    def _field_addr(self, bank_base: int, row: int) -> int:
        return runtime._yami_hover_icon_field_addr(bank_base, row)

    def _seed_stock_rows(self) -> None:
        for _bank_label, bank_base in runtime.YAMI_HOVER_ICON_ROW_BANKS:
            self.mem.halfwords[self._field_addr(bank_base, 26)] = 0x001B
            for target_row, stock_value in runtime.YAMI_HOVER_ICON_STOCK_TARGET_IDS.items():
                self.mem.halfwords[self._field_addr(bank_base, target_row)] = stock_value

    def test_inserted_yami_rows_all_borrow_stock_ryu_b26(self) -> None:
        self.assertEqual(
            runtime.YAMI_HOVER_ICON_ID_PLAN,
            (
                ("Yami 3 hover icon -> Ryu", 10, 26, 0x001B),
                ("Yami 2 hover icon -> Ryu", 16, 26, 0x001B),
                ("Yami 1 hover icon -> Ryu", 18, 26, 0x001B),
            ),
        )

    def test_install_changes_only_six_yami_material_ids(self) -> None:
        wrote, failed = runtime._install_yami_hover_icon_id_route()

        self.assertEqual((wrote, failed), (6, 0))
        expected_addrs = {
            self._field_addr(bank_base, row)
            for _bank_label, bank_base in runtime.YAMI_HOVER_ICON_ROW_BANKS
            for row in (10, 16, 18)
        }
        self.assertEqual({addr for addr, _payload in self.mem.writes}, expected_addrs)
        for addr in expected_addrs:
            self.assertEqual(self.mem.halfwords[addr], 0x001B)

    def test_restore_returns_the_six_target_rows_to_stock(self) -> None:
        self.assertEqual((6, 0), runtime._install_yami_hover_icon_id_route())
        self.mem.writes.clear()

        wrote, failed = runtime._restore_yami_hover_icon_id_route_only()

        self.assertEqual((wrote, failed), (6, 0))
        for _bank_label, bank_base in runtime.YAMI_HOVER_ICON_ROW_BANKS:
            for row, stock_value in runtime.YAMI_HOVER_ICON_STOCK_TARGET_IDS.items():
                self.assertEqual(self.mem.halfwords[self._field_addr(bank_base, row)], stock_value)


if __name__ == "__main__":
    unittest.main()
