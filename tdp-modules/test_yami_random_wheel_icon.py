from __future__ import annotations

import unittest

import char_test_runtime as runtime


class _VirtualDolphinMemory:
    def __init__(self) -> None:
        self.words: dict[int, int] = {}
        self.bytes: dict[int, bytes] = {}
        self.originals: dict[int, int] = {}

        # The B13 source is deliberately distinct in each BRRES bank.  This
        # catches accidental hard-coding of a stale texture pointer.
        for source_index, (_label, binding_addr, header_addr) in enumerate(
            runtime.YAMI_WHEEL_RANDOM_SOURCE_ROWS
        ):
            binding_ptr = 0x91000000 + source_index * 0x200
            header_ptr = 0x91000100 + source_index * 0x200
            self.words[binding_addr] = binding_ptr
            self.words[header_addr] = header_ptr
            self.bytes[binding_ptr] = runtime.YAMI_WHEEL_RANDOM_TEX0_MAGIC
            self.bytes[header_ptr] = runtime.YAMI_WHEEL_RANDOM_TEX0_MAGIC

        for index, (_label, _source_index, binding_addr, header_addr) in enumerate(
            runtime.YAMI_WHEEL_RANDOM_TARGET_ROWS
        ):
            binding_ptr = 0x92000000 + index * 0x200
            header_ptr = 0x92000100 + index * 0x200
            self.words[binding_addr] = binding_ptr
            self.words[header_addr] = header_ptr
            self.originals[binding_addr] = binding_ptr
            self.originals[header_addr] = header_ptr
            self.bytes[binding_ptr] = runtime.YAMI_WHEEL_RANDOM_TEX0_MAGIC
            self.bytes[header_ptr] = runtime.YAMI_WHEEL_RANDOM_TEX0_MAGIC

    def read(self, addr: int, size: int) -> bytes:
        value = self.bytes.get(addr, b"")
        if size <= len(value):
            return value[:size]
        return value + (b"\x00" * (size - len(value))) if value else b""

    def read_u32(self, addr: int) -> int | None:
        return self.words.get(addr)

    def write_u32(self, addr: int, value: int) -> bool:
        if addr not in self.words:
            return False
        self.words[addr] = int(value) & 0xFFFFFFFF
        return True


class YamiRandomWheelIconTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mem = _VirtualDolphinMemory()
        self.saved = {
            "_safe_read": runtime._safe_read,
            "_safe_read_u32be": runtime._safe_read_u32be,
            "_safe_write_u32be": runtime._safe_write_u32be,
            "state": dict(runtime._ROSTER_STATE),
            "session": dict(runtime._YAMI_WHEEL_RANDOM_ICON_SESSION),
        }
        runtime._safe_read = self.mem.read
        runtime._safe_read_u32be = self.mem.read_u32
        runtime._safe_write_u32be = self.mem.write_u32
        runtime._clear_yami_wheel_random_icon_session()

    def tearDown(self) -> None:
        runtime._safe_read = self.saved["_safe_read"]
        runtime._safe_read_u32be = self.saved["_safe_read_u32be"]
        runtime._safe_write_u32be = self.saved["_safe_write_u32be"]
        runtime._ROSTER_STATE.clear()
        runtime._ROSTER_STATE.update(self.saved["state"])
        runtime._YAMI_WHEEL_RANDOM_ICON_SESSION.clear()
        runtime._YAMI_WHEEL_RANDOM_ICON_SESSION.update(self.saved["session"])

    def test_stock_random_b13_is_copied_to_all_yami_thumbnail_pointers(self) -> None:
        wrote, failed = runtime._install_yami_wheel_random_icon_route()
        self.assertEqual(0, failed)
        self.assertEqual(12, wrote)

        sources = tuple(
            (self.mem.read_u32(binding_addr), self.mem.read_u32(header_addr))
            for _label, binding_addr, header_addr in runtime.YAMI_WHEEL_RANDOM_SOURCE_ROWS
        )
        for _label, source_index, binding_addr, header_addr in runtime.YAMI_WHEEL_RANDOM_TARGET_ROWS:
            expected_binding, expected_header = sources[source_index]
            self.assertEqual(expected_binding, self.mem.read_u32(binding_addr))
            self.assertEqual(expected_header, self.mem.read_u32(header_addr))
        self.assertTrue(runtime._yami_wheel_random_icon_route_status()["installed"])

    def test_restore_returns_the_exact_pre_patch_pointers(self) -> None:
        self.assertEqual((12, 0), runtime._install_yami_wheel_random_icon_route())
        restored, failed = runtime._restore_yami_wheel_random_icon_route_only()
        self.assertEqual(0, failed)
        self.assertEqual(12, restored)
        for addr, original in self.mem.originals.items():
            self.assertEqual(original, self.mem.read_u32(addr))

    def test_mixed_binding_state_refuses_to_layer_a_write(self) -> None:
        _label, source_index, binding_addr, header_addr = runtime.YAMI_WHEEL_RANDOM_TARGET_ROWS[0]
        source_binding, source_header = (
            self.mem.read_u32(runtime.YAMI_WHEEL_RANDOM_SOURCE_ROWS[source_index][1]),
            self.mem.read_u32(runtime.YAMI_WHEEL_RANDOM_SOURCE_ROWS[source_index][2]),
        )
        self.mem.words[binding_addr] = source_binding
        # Header remains its original non-Random pointer, producing a dangerous
        # half-patched state.  The installer must refuse it.
        self.assertNotEqual(source_header, self.mem.read_u32(header_addr))

        wrote, failed = runtime._install_yami_wheel_random_icon_route()
        self.assertEqual(0, wrote)
        self.assertEqual(1, failed)
        self.assertEqual(source_binding, self.mem.read_u32(binding_addr))


if __name__ == "__main__":
    unittest.main(verbosity=2)
