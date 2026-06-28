from __future__ import annotations

import unittest

import tvcgui.features.character_select.runtime as runtime


class _VirtualDolphinMemory:
    def __init__(self) -> None:
        self.seq_base = runtime.CHRSEL_SEQ_HEAP_BASE
        self.seq = bytearray(b"\x00" * runtime.CHRSEL_SEQ_HEAP_SIZE)
        self.brres_base = runtime.YAMI_RUNTIME_PREVIEW_BRRES_1022_BASE
        self.brres = bytearray(b"\x00" * 0x800)

        sig_addr = runtime.CHRSEL_SEQ_SIGNATURE_OFF
        self.seq[sig_addr:sig_addr + len(runtime.CHRSEL_SEQ_SIGNATURE)] = runtime.CHRSEL_SEQ_SIGNATURE
        self.brres[:4] = runtime.YAMI_RUNTIME_PREVIEW_BRRES_MAGIC
        field_off = runtime.YAMI_RUNTIME_PREVIEW_SELECT_SIL_FIELD_OFF
        self.brres[field_off:field_off + 4] = runtime.YAMI_RUNTIME_PREVIEW_SELECT_SIL_OFFSET.to_bytes(4, "big")

        # Exact hidden-handler count observed in the supplied 2026-06-27 dump.
        select_offsets = (0x20, 0x98, 0xA4, 0x290, 0x420, 0x498, 0x4A4, 0x6B0)
        name_offsets = (0x54, 0xE4, 0x24C, 0x2DC, 0x454, 0x4E4, 0x66C, 0x6FC)
        window_base = runtime.YAMI_RUNTIME_PREVIEW_SEQ_START_OFF
        for offset in select_offsets:
            addr = window_base + offset
            value = runtime.YAMI_RUNTIME_PREVIEW_SELECT_OLD
            self.seq[addr:addr + len(value)] = value
        for offset in name_offsets:
            addr = window_base + offset
            value = runtime.YAMI_RUNTIME_PREVIEW_NAME_OLD
            self.seq[addr:addr + len(value)] = value

    def read(self, addr: int, size: int) -> bytes:
        if self.seq_base <= addr and addr + size <= self.seq_base + len(self.seq):
            off = addr - self.seq_base
            return bytes(self.seq[off:off + size])
        if self.brres_base <= addr and addr + size <= self.brres_base + len(self.brres):
            off = addr - self.brres_base
            return bytes(self.brres[off:off + size])
        return b""

    def write(self, addr: int, payload: bytes) -> bool:
        data = bytes(payload)
        if self.seq_base <= addr and addr + len(data) <= self.seq_base + len(self.seq):
            off = addr - self.seq_base
            self.seq[off:off + len(data)] = data
            return True
        if self.brres_base <= addr and addr + len(data) <= self.brres_base + len(self.brres):
            off = addr - self.brres_base
            self.brres[off:off + len(data)] = data
            return True
        return False


class YamiRuntimeSceneRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mem = _VirtualDolphinMemory()
        self.saved = {
            "_safe_read": runtime._safe_read,
            "_safe_read_u32be": runtime._safe_read_u32be,
            "_safe_write_bytes": runtime._safe_write_bytes,
            "_safe_write_u32be": runtime._safe_write_u32be,
            "state": dict(runtime._ROSTER_STATE),
            "word_originals": dict(runtime._ROSTER_ORIGINALS),
            "byte_originals": dict(runtime._ROSTER_BYTE_ORIGINALS),
        }
        runtime._ROSTER_ORIGINALS.clear()
        runtime._ROSTER_BYTE_ORIGINALS.clear()
        runtime._safe_read = self.mem.read
        runtime._safe_read_u32be = lambda addr: (
            int.from_bytes(self.mem.read(addr, 4), "big")
            if len(self.mem.read(addr, 4)) == 4 else None
        )
        runtime._safe_write_bytes = self.mem.write
        runtime._safe_write_u32be = lambda addr, value: self.mem.write(
            addr, int(value).to_bytes(4, "big")
        )

    def tearDown(self) -> None:
        runtime._safe_read = self.saved["_safe_read"]
        runtime._safe_read_u32be = self.saved["_safe_read_u32be"]
        runtime._safe_write_bytes = self.saved["_safe_write_bytes"]
        runtime._safe_write_u32be = self.saved["_safe_write_u32be"]
        runtime._ROSTER_STATE.clear()
        runtime._ROSTER_STATE.update(self.saved["state"])
        runtime._ROSTER_ORIGINALS.clear()
        runtime._ROSTER_ORIGINALS.update(self.saved["word_originals"])
        runtime._ROSTER_BYTE_ORIGINALS.clear()
        runtime._ROSTER_BYTE_ORIGINALS.update(self.saved["byte_originals"])

    def test_dump_contract_classifies_the_unpatched_hidden_route(self) -> None:
        window = self.mem.read(
            runtime.CHRSEL_SEQ_HEAP_BASE + runtime.YAMI_RUNTIME_PREVIEW_SEQ_START_OFF,
            runtime.YAMI_RUNTIME_PREVIEW_SEQ_END_OFF - runtime.YAMI_RUNTIME_PREVIEW_SEQ_START_OFF,
        )
        status = runtime._classify_yami_runtime_preview_route(
            window,
            runtime.YAMI_RUNTIME_PREVIEW_SELECT_SIL_OFFSET,
        )
        self.assertTrue(status["fresh"])
        self.assertFalse(status["installed"])
        self.assertEqual(8, len(status["old_select_offsets"]))
        self.assertEqual(8, len(status["old_name_offsets"]))

    def test_install_and_restore_touch_only_hidden_scene_route(self) -> None:
        wrote, failed = runtime._install_yami_runtime_preview_route()
        self.assertEqual(0, failed)
        self.assertEqual(16, wrote)

        installed = runtime._yami_runtime_preview_route_status()
        self.assertTrue(installed["installed"])
        self.assertFalse(installed["fresh"])
        self.assertEqual(
            runtime.YAMI_RUNTIME_PREVIEW_SELECT_SIL_OFFSET,
            runtime._safe_read_u32be(runtime.YAMI_RUNTIME_PREVIEW_SELECT_SIL_FIELD_ADDR),
        )

        restored, restore_failed = runtime._restore_yami_runtime_preview_route_only()
        self.assertEqual(0, restore_failed)
        self.assertEqual(16, restored)
        fresh = runtime._yami_runtime_preview_route_status()
        self.assertTrue(fresh["fresh"])
        self.assertFalse(fresh["installed"])

    def test_legacy_random_alias_refuses_blank_route(self) -> None:
        field_addr = runtime.YAMI_RUNTIME_PREVIEW_SELECT_SIL_FIELD_ADDR
        self.assertTrue(self.mem.write(
            field_addr,
            runtime.YAMI_RUNTIME_PREVIEW_SELECT_RANDOM0_OFFSET.to_bytes(4, "big"),
        ))
        wrote, failed = runtime._install_yami_runtime_preview_route()
        self.assertEqual((0, 1), (wrote, failed))
        self.assertFalse(runtime._yami_runtime_preview_route_status()["native_sil"])

    def test_mixed_handler_window_refuses_write(self) -> None:
        # A foreign/partially modified script must not be stacked on top of.
        pos = runtime.YAMI_RUNTIME_PREVIEW_SEQ_START_OFF + 0x20
        self.mem.seq[pos:pos + len(runtime.YAMI_RUNTIME_PREVIEW_SELECT_OLD)] = b"select_bad\x00"
        wrote, failed = runtime._install_yami_runtime_preview_route()
        self.assertEqual(0, wrote)
        self.assertEqual(1, failed)
        self.assertEqual(
            runtime.YAMI_RUNTIME_PREVIEW_SELECT_SIL_OFFSET,
            runtime._safe_read_u32be(runtime.YAMI_RUNTIME_PREVIEW_SELECT_SIL_FIELD_ADDR),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
