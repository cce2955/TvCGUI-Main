#!/usr/bin/env python3

from __future__ import annotations

import math
import struct
import time
from dataclasses import dataclass
from typing import Dict, Optional

import dolphin_io


PROJECTILE_POOLS = [
    0x91B15900,
    0x91B15A10,
    0x91B15B50,
    0x91B15C90,
    0x91B15DD0,
    0x91B15F10,
]

PROJECTILE_NODE_STRIDE = 0x30
PROJECTILE_NODE_COUNT = 16

PROJ_OFF_X = 0x00
PROJ_OFF_Y = 0x10
PROJ_OFF_Z = 0x20
PROJ_OFF_DIM_0 = 0x08
PROJ_OFF_DIM_1 = 0x18
PROJ_OFF_DIM_2 = 0x28

SAMPLE_HZ = 60.0
PRINT_INTERVAL = 0.20
TOP_N = 24
MIN_CHANGE_TO_SHOW = 0.0005


def log(msg: str) -> None:
    print(msg, flush=True)


def _rf(addr: int) -> float:
    v = dolphin_io.rd32(addr)
    if v is None:
        return 0.0
    try:
        f = struct.unpack(">f", struct.pack(">I", v))[0]
        return f if math.isfinite(f) else 0.0
    except Exception:
        return 0.0


def smart_hook() -> None:
    dme = getattr(dolphin_io, "dme", None)
    if dme is None:
        raise RuntimeError("dolphin_io.dme not found")

    if dme.is_hooked():
        log("[hook] already hooked")
        return

    log("[hook] calling dme.hook() directly")
    t0 = time.perf_counter()
    dme.hook()
    dt = time.perf_counter() - t0

    if not dme.is_hooked():
        raise RuntimeError("dme.hook() returned but is_hooked() is false")

    log(f"[hook] hooked in {dt:.3f}s")


@dataclass
class NodeSample:
    node_addr: int
    pool_addr: int
    pool_index: int
    node_index: int
    x: float
    y: float
    z: float
    d0: float
    d1: float
    d2: float


@dataclass
class NodeTrack:
    node_addr: int
    last_x: Optional[float] = None
    last_y: Optional[float] = None
    last_z: Optional[float] = None
    last_t: Optional[float] = None

    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0

    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0

    speed_xy: float = 0.0
    initialized: bool = False

    def update(self, s: NodeSample, now: float) -> None:
        if self.initialized and self.last_t is not None:
            dt = now - self.last_t
            if dt > 0.0:
                self.dx = s.x - self.last_x
                self.dy = s.y - self.last_y
                self.dz = s.z - self.last_z
                self.vx = self.dx / dt
                self.vy = self.dy / dt
                self.vz = self.dz / dt
                self.speed_xy = math.sqrt(self.vx * self.vx + self.vy * self.vy)

        self.last_x = s.x
        self.last_y = s.y
        self.last_z = s.z
        self.last_t = now
        self.initialized = True


def read_all_pool_nodes() -> list[NodeSample]:
    out: list[NodeSample] = []

    for pool_index, pool in enumerate(PROJECTILE_POOLS):
        for node_index in range(PROJECTILE_NODE_COUNT):
            node_addr = pool + node_index * PROJECTILE_NODE_STRIDE

            x = _rf(node_addr + PROJ_OFF_X)
            y = _rf(node_addr + PROJ_OFF_Y)
            z = _rf(node_addr + PROJ_OFF_Z)
            d0 = _rf(node_addr + PROJ_OFF_DIM_0)
            d1 = _rf(node_addr + PROJ_OFF_DIM_1)
            d2 = _rf(node_addr + PROJ_OFF_DIM_2)

            out.append(
                NodeSample(
                    node_addr=node_addr,
                    pool_addr=pool,
                    pool_index=pool_index,
                    node_index=node_index,
                    x=x,
                    y=y,
                    z=z,
                    d0=d0,
                    d1=d1,
                    d2=d2,
                )
            )

    return out


def update_tracks(tracks: Dict[int, NodeTrack], samples: list[NodeSample]) -> None:
    now = time.perf_counter()

    for s in samples:
        if s.node_addr not in tracks:
            tracks[s.node_addr] = NodeTrack(node_addr=s.node_addr)
        tracks[s.node_addr].update(s, now)


def dump_nodes(tracks: Dict[int, NodeTrack], samples: list[NodeSample]) -> None:
    rows = []
    changed = []

    for s in samples:
        t = tracks.get(s.node_addr)
        if t is None:
            continue

        row = (s, t)
        rows.append(row)

        if abs(t.dx) >= MIN_CHANGE_TO_SHOW or abs(t.dy) >= MIN_CHANGE_TO_SHOW:
            changed.append(row)

    log("")
    log("=" * 146)
    log(f"total_nodes={len(rows)} changed_nodes={len(changed)}")

    target = changed if changed else rows
    target.sort(key=lambda row: (abs(row[1].dx) + abs(row[1].dy)), reverse=True)

    log("node_addr     pool         idx    x        y        z       dx       dy        vx        vy   speed_xy       d0        d1        d2")
    log("-" * 146)

    for s, t in target[:TOP_N]:
        log(
            f"0x{s.node_addr:08X}  "
            f"0x{s.pool_addr:08X}  "
            f"{s.node_index:02d}  "
            f"{s.x:8.4f} {s.y:8.4f} {s.z:8.4f} "
            f"{t.dx:8.4f} {t.dy:8.4f} "
            f"{t.vx:8.4f} {t.vy:8.4f} {t.speed_xy:9.4f} "
            f"{s.d0:9.4f} {s.d1:9.4f} {s.d2:9.4f}"
        )


def main() -> None:
    log("[main] raw pool tracker")
    smart_hook()

    tracks: Dict[int, NodeTrack] = {}
    sample_interval = 1.0 / SAMPLE_HZ
    last_dump = 0.0

    log("[main] baseline first, then fire projectile")
    log("[main] this reads all nodes, no active filtering")

    while True:
        loop_start = time.perf_counter()

        samples = read_all_pool_nodes()
        update_tracks(tracks, samples)

        now = time.perf_counter()
        if now - last_dump >= PRINT_INTERVAL:
            dump_nodes(tracks, samples)
            last_dump = now

        elapsed = time.perf_counter() - loop_start
        sleep_for = sample_interval - elapsed
        if sleep_for > 0:
            time.sleep(sleep_for)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("[main] exiting")