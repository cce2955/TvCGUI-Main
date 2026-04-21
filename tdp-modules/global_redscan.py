# global_redscan.py
# Memory-efficient full-RAM correlation scanner.
#
# Hotkeys in main.py:
#   F3 -> snapshot()
#   F4 -> analyze()
#
# How this version works:
#   - We track per-region "previous raw bytes".
#   - On each snapshot:
#       * Read each region in chunks.
#       * If it's the first time seeing that region, just store the raw bytes.
#         (No per-address series yet; we can't diff yet.)
#       * Otherwise, compare new bytes against previous bytes and ONLY record
#         addresses where the byte changed.
#   - We associate those changed-byte values with the current HP value.
#
# Effect:
#   - The first snapshot mostly just seeds baselines.
#   - Later snapshots only add the handful of addresses that actually change
#     frame-to-frame / health-step-to-health-step (HUD, meters, etc.)
#
# Analysis:
#   - For each address that ever changed, we build its byte history over all
#     snapshots and correlate it to HP (same_dir / inv_dir).
#
# This reduces memory from tens of millions of addresses per snapshot
# to usually just a few hundred or thousand, which avoids MemoryError.


import time
from dolphin_io import rbytes


MEM_REGIONS = [
    (0x80000000, 0x81800000),  # MEM1 (24MB)
    (0x90000000, 0x94000000),  # MEM2 (up to 64MB, depends on game usage)
]

CHUNK_SIZE = 64 * 1024  # 64KB chunks


class GlobalRedScanner:
    def __init__(self):
        # For each region we remember the last raw dump so we can diff.
        # prev_raw[ (start,end) ] = b"...full_region_bytes..."
        self.prev_raw = {}

        # timeseries for addresses that actually move:
        # addr_hist[addr] = [ (hp_val_at_snapshot, byte_val_at_snapshot), ... ]
        #
        # We don't store HP separately per snapshot anymore; we attach HP to each addr sample.
        self.addr_hist = {}

        # just for logging
        self.snap_count = 0

    def _read_region_full(self, start_addr, end_addr):
        """
        Read an entire region [start_addr, end_addr) and return a single bytes object.
        We'll stitch per-chunk reads together.
        Returns b"" if we can't read anything.
        """
        parts = []
        addr = start_addr
        while addr < end_addr:
            size = min(CHUNK_SIZE, end_addr - addr)
            blob = rbytes(addr, size)
            if not blob:
                # still advance; unreadable pages just contribute empty
                parts.append(b"\x00" * size)
            else:
                # make sure length matches so alignment stays correct
                if len(blob) < size:
                    blob = blob + (b"\x00" * (size - len(blob)))
                parts.append(blob)
            addr += size
        return b"".join(parts)

    def snapshot(self, fighter_snap):
        """
        Grab current HP from fighter_snap, then diff each memory region
        against the last snapshot of that region. Record only changed bytes.
        """
        if not fighter_snap:
            print("global_redscan: no fighter_snap, skip snapshot")
            return

        hp_val = fighter_snap.get("cur")
        hp_max = fighter_snap.get("max")
        if hp_val is None or hp_max in (None, 0):
            print("global_redscan: invalid hp in snap, skip snapshot")
            return

        self.snap_count += 1
        hp_now = hp_val

        total_changed_addrs = 0
        print("global_redscan: diffing memory regions...")

        for (start_addr, end_addr) in MEM_REGIONS:
            new_blob = self._read_region_full(start_addr, end_addr)
            if not new_blob:
                continue

            prev_blob = self.prev_raw.get((start_addr, end_addr))

            # First time we've seen this region: just store baseline.
            if prev_blob is None:
                self.prev_raw[(start_addr, end_addr)] = new_blob
                continue

            # Diff byte-by-byte and record only addresses that changed
            # (this is the big memory win).
            region_len = min(len(prev_blob), len(new_blob))
            for i in range(region_len):
                old_b = prev_blob[i]
                new_b = new_blob[i]
                if old_b == new_b:
                    continue

                abs_addr = start_addr + i
                # Record this address + current HP + new byte value.
                self.addr_hist.setdefault(abs_addr, []).append(
                    (hp_now, new_b)
                )
                total_changed_addrs += 1

            # update baseline to current
            self.prev_raw[(start_addr, end_addr)] = new_blob

        print(
            f"global_redscan: snapshot {self.snap_count} "
            f"done, tracked {total_changed_addrs} changed bytes"
        )

    def analyze(self, top_n=40):
        """
        Build per-address byte series and correlate with HP.
        We're looking for addresses whose byte values track HP
        (same_dir) or track "missing HP" (inv_dir).
        """
        # addr_hist[addr] = [(hp,val), (hp,val), ...]
        # We need >=2 samples per addr to talk about direction.
        scored = []

        for addr, pairs in self.addr_hist.items():
            if len(pairs) < 2:
                continue

            # unzip into aligned hp_series / val_series
            hp_series  = [p[0] for p in pairs]
            val_series = [p[1] for p in pairs]

            # throw out addresses that never actually changed after recording
            if len(set(val_series)) <= 1:
                continue

            same_dir = 0
            inv_dir  = 0
            changes  = 0
            for i in range(len(val_series) - 1):
                dhp = hp_series[i+1]  - hp_series[i]
                dv  = val_series[i+1] - val_series[i]
                if dv != 0:
                    changes += 1
                if dhp == 0 or dv == 0:
                    continue
                if (dhp > 0 and dv > 0) or (dhp < 0 and dv < 0):
                    same_dir += 1
                if (dhp > 0 and dv < 0) or (dhp < 0 and dv > 0):
                    inv_dir += 1

            distinct_values = len(set(val_series))

            scored.append((
                inv_dir,
                same_dir,
                changes,
                distinct_values,
                addr,
                val_series,
                hp_series,
            ))

        # Sort most interesting first:
        # 1. high inv_dir (value rises as HP falls → "damage taken")
        # 2. then high same_dir (value falls with HP → "remaining life")
        # 3. then # of changes, then how many distinct values
        scored.sort(
            key=lambda row: (row[0], row[1], row[2], row[3]),
            reverse=True
        )

        print("===== global_redscan analysis =====")
        for row in scored[:top_n]:
            inv_dir, same_dir, changes, distinct_values, addr, vs, hs = row
            print(f"addr 0x{addr:08X} [byte] = {vs}")
            print(
                f"    hp  = {hs}\n"
                f"    same_dir={same_dir} inv_dir={inv_dir} "
                f"changes={changes} distinct={distinct_values}"
            )
        print("===== end global_redscan =====")
        print("Heuristic:")
        print("- High inv_dir  -> rises when HP drops (red/white life style)")
        print("- High same_dir -> falls when HP drops (pooled/real life mirror)")
        print("- We only kept bytes that actually changed between snapshots")
