"""Focused contract checks for the DOL UI-tag remap route."""
from __future__ import annotations

import char_test_runtime as runtime


class FakeMemory:
    def __init__(self) -> None:
        self.words: dict[int, int] = {}
        self.bytes: dict[int, int] = {}

    def write_bytes(self, address: int, data: bytes) -> bool:
        for index, value in enumerate(data):
            self.bytes[address + index] = value
        return True

    def read_bytes(self, address: int, size: int) -> bytes:
        return bytes(self.bytes.get(address + index, 0) for index in range(size))

    def write_word(self, address: int, value: int) -> bool:
        self.words[address] = value & 0xFFFFFFFF
        return True

    def read_word(self, address: int) -> int | None:
        return self.words.get(address)


def make_stock_memory() -> FakeMemory:
    memory = FakeMemory()
    for _icon_id, (pointer, tag) in runtime.DOL_TAG_POINTERS.items():
        memory.write_bytes(pointer, tag)
    for base, stock in (
        (runtime.DOL_CHAR_TAG_MAP_BASE, runtime._DOL_STOCK_DIRECT_TAG_POINTERS),
        (runtime.DOL_CANONICAL_UI_TAG_MAP_BASE, runtime._DOL_STOCK_CANONICAL_TAG_POINTERS),
    ):
        for fighter_id, pointer in stock.items():
            memory.write_word(base + fighter_id * 4, pointer)
    return memory


def install_memory_hooks(memory: FakeMemory) -> None:
    runtime._safe_read = memory.read_bytes
    runtime._safe_read_u32be = memory.read_word
    runtime._safe_write_u32be = memory.write_word


def test_install_and_restore() -> None:
    memory = make_stock_memory()
    install_memory_hooks(memory)
    runtime._clear_yami_dol_icon_tag_session()

    status = runtime._dol_icon_tag_route_status()
    assert status["ready"] and status["fresh"] and not status["installed"]

    wrote, failed = runtime._install_yami_dol_icon_tag_route()
    assert (wrote, failed) == (8, 0)
    assert runtime._dol_icon_tag_route_status()["installed"]

    ryu_ptr = runtime.DOL_TAG_POINTERS[runtime.RYU_VISUAL_PROXY_ID][0]
    zero_ptr = runtime.DOL_TAG_POINTERS[runtime.ZERO_VISUAL_PROXY_ID][0]
    for base in (runtime.DOL_CHAR_TAG_MAP_BASE, runtime.DOL_CANONICAL_UI_TAG_MAP_BASE):
        assert memory.read_word(base + 0x00 * 4) == zero_ptr
        assert memory.read_word(base + 0x17 * 4) == ryu_ptr
        assert memory.read_word(base + 0x18 * 4) == ryu_ptr
        assert memory.read_word(base + 0x19 * 4) == ryu_ptr

    wrote, failed = runtime._restore_yami_dol_icon_tag_route_only()
    assert (wrote, failed) == (8, 0)
    assert runtime._dol_icon_tag_route_status()["fresh"]


def test_migrates_the_previous_cmn_ts2_fra_route_to_ryu() -> None:
    memory = make_stock_memory()
    install_memory_hooks(memory)
    runtime._clear_yami_dol_icon_tag_session()
    for base in (runtime.DOL_CHAR_TAG_MAP_BASE, runtime.DOL_CANONICAL_UI_TAG_MAP_BASE):
        for fighter_id, pointer in runtime._DOL_LEGACY_PRESENTATION_POINTERS.items():
            memory.write_word(base + fighter_id * 4, pointer)
    status = runtime._dol_icon_tag_route_status()
    assert status["ready"] and status["migratable_legacy"] and not status["fresh"]
    wrote, failed = runtime._install_yami_dol_icon_tag_route()
    assert (wrote, failed) == (8, 0)
    assert runtime._dol_icon_tag_route_status()["installed"]


def test_refuses_foreign_value() -> None:
    memory = make_stock_memory()
    install_memory_hooks(memory)
    foreign_addr = runtime.DOL_CHAR_TAG_MAP_BASE + 0x18 * 4
    memory.write_word(foreign_addr, 0xDEADBEEF)
    status = runtime._dol_icon_tag_route_status()
    assert not status["ready"] and status["mixed"]
    wrote, failed = runtime._install_yami_dol_icon_tag_route()
    assert wrote == 0 and failed == 1
    assert memory.read_word(foreign_addr) == 0xDEADBEEF


if __name__ == "__main__":
    test_install_and_restore()
    test_migrates_the_previous_cmn_ts2_fra_route_to_ryu()
    test_refuses_foreign_value()
    print("3 focused DOL tag-map tests passed (including Solo null -> Zero)")
