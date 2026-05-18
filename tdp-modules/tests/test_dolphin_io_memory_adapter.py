from __future__ import annotations

import importlib
import math
import struct
import sys
import types
import unittest


class FakeDME(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("dolphin_memory_engine")
        self.hooked = False
        self.hook_calls = 0
        self.memory: dict[int, int] = {}
        self.write_log: list[tuple[int, bytes]] = []

    def is_hooked(self) -> bool:
        return self.hooked

    def hook(self) -> None:
        self.hook_calls += 1
        self.hooked = True

    def read_bytes(self, addr: int, size: int) -> bytes:
        return bytes(self.memory.get(addr + i, 0) for i in range(size))

    def write_bytes(self, addr: int, data: bytes) -> None:
        payload = bytes(data)
        self.write_log.append((addr, payload))
        for i, b in enumerate(payload):
            self.memory[addr + i] = b


class DolphinIOMemoryAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_dme = FakeDME()
        self.old_dme = sys.modules.get("dolphin_memory_engine")
        sys.modules["dolphin_memory_engine"] = self.fake_dme
        sys.modules.pop("dolphin_io", None)
        self.dolphin_io = importlib.import_module("dolphin_io")
        # Force the simple DME path so this test is portable and never touches
        # the host process table while still validating public read/write logic.
        self.dolphin_io._IS_WINDOWS = False

    def tearDown(self) -> None:
        sys.modules.pop("dolphin_io", None)
        if self.old_dme is not None:
            sys.modules["dolphin_memory_engine"] = self.old_dme
        else:
            sys.modules.pop("dolphin_memory_engine", None)

    def test_hook_calls_dme_hook_until_connected(self) -> None:
        self.assertFalse(self.fake_dme.hooked)

        self.dolphin_io.hook()

        self.assertTrue(self.fake_dme.hooked)
        self.assertEqual(1, self.fake_dme.hook_calls)

    def test_addr_in_ram_accepts_mem1_and_mem2_only(self) -> None:
        self.assertTrue(self.dolphin_io.addr_in_ram(0x80000000))
        self.assertTrue(self.dolphin_io.addr_in_ram(0x817FFFFF))
        self.assertTrue(self.dolphin_io.addr_in_ram(0x90000000))
        self.assertTrue(self.dolphin_io.addr_in_ram(0x93FFFFFF))
        self.assertFalse(self.dolphin_io.addr_in_ram(0x81800000))
        self.assertFalse(self.dolphin_io.addr_in_ram(0x94000000))
        self.assertFalse(self.dolphin_io.addr_in_ram(None))

    def test_big_endian_read_helpers(self) -> None:
        base = 0x90000100
        raw = bytes.fromhex("12 34 56 78 3F 80 00 00")
        for i, b in enumerate(raw):
            self.fake_dme.memory[base + i] = b

        self.assertEqual(0x12, self.dolphin_io.rd8(base))
        self.assertEqual(0x12345678, self.dolphin_io.rd32(base))
        self.assertEqual(1.0, self.dolphin_io.rdf32(base + 4))

    def test_write_helpers_emit_big_endian_payloads(self) -> None:
        base = 0x90000200

        self.assertTrue(self.dolphin_io.wd8(base, 0x1FF))
        self.assertTrue(self.dolphin_io.wd32(base + 4, 0x12345678))
        self.assertTrue(self.dolphin_io.wdf32(base + 8, 1.5))

        self.assertEqual(bytes([0xFF]), self.fake_dme.write_log[0][1])
        self.assertEqual(bytes.fromhex("12 34 56 78"), self.fake_dme.write_log[1][1])
        self.assertEqual(struct.pack(">f", 1.5), self.fake_dme.write_log[2][1])
        self.assertFalse(self.dolphin_io.wdf32(base + 12, math.inf))

    def test_reads_are_clamped_at_mem2_high_edge(self) -> None:
        self.fake_dme.memory[0x93FFFFFE] = 0xAA
        self.fake_dme.memory[0x93FFFFFF] = 0xBB

        data = self.dolphin_io.rbytes(0x93FFFFFE, 8)

        self.assertEqual(bytes.fromhex("AA BB"), data)


if __name__ == "__main__":
    unittest.main()
