# redscan.py
# Local fighter-struct scanner.
#
# Goal:
#   Capture a history of bytes around a fighter's base pointer so we can see
#   which offsets behave like HP / pooled HP / mystery state.
#
# Usage pattern in main loop:
#   - Press F1 to snapshot the current P1-C1 fighter block
#   - Press F2 to analyze all collected snapshots
#
# This is "close range" around a single fighter struct, not full RAM.
# We currently scan offsets 0x000..0x0FF from that fighter base.

import time
from dolphin_io import rd8

# How wide around the fighter struct we sample each time.
# You can widen this to 0x200/0x400 later if you want.
SCAN_START = 0x000
SCAN_END   = 0x100  # not inclusive; grabs [0x000 .. 0x0FF]


class RedHealthScanner:
    """
    Collects snapshots of a single fighter's struct bytes across time
    along with that fighter's HP values. Then runs a heuristic to spot
    offsets that move with HP or against HP.
    """

    def __init__(self):
        # samples = [
        #   {
        #     "t": timestamp,
        #     "hp_val": cur HP int,
        #     "hp_pct": cur/max 0..1,
        #     "blob": { offset:int -> byte:int }
        #   },
        #   ...
        # ]
        self.samples = []

    def snapshot(self, fighter_snap):
        """
        fighter_snap is the dict from read_fighter() for a slot (usually P1-C1).
        We expect:
          fighter_snap["base"] == base address of fighter struct
          fighter_snap["cur"]  == current HP int
          fighter_snap["max"]  == max HP int
        """
        base = fighter_snap.get("base")
        hp   = fighter_snap.get("cur")
        mx   = fighter_snap.get("max")

        if not base or hp is None or mx is None or mx <= 0:
            print("redscan: invalid fighter snapshot, skip")
            return

        hp_pct = float(hp) / float(mx)

        blob = {}
        for off in range(SCAN_START, SCAN_END):
            blob[off] = rd8(base + off)

        self.samples.append({
            "t": time.time(),
            "hp_val": hp,
            "hp_pct": hp_pct,
            "blob": blob,
        })

    def analyze(self, top_n=32):
        """
        Find interesting offsets.
        Heuristic:
        - Build the timeseries for each offset across all snapshots.
        - Score how often it changes in the SAME direction as HP vs the OPPOSITE.
          - same_dir: value goes down when HP goes down
          - inv_dir:  value goes up when HP goes down
        - Print the top N offsets sorted by (inv_dir first, then same_dir).
        """
        if len(self.samples) < 2:
            print("redscan: not enough samples to analyze")
            return

        hp_list = [s["hp_val"] for s in self.samples]

        # Build per-offset historical data
        hist = {}  # off -> [vals...]
        for s in self.samples:
            blob = s["blob"]
            for off, val in blob.items():
                hist.setdefault(off, []).append(val)

        # Score each offset
        results = []
        for off, series in hist.items():
            # skip constant values
            if len(set(series)) == 1:
                continue

            same_dir = 0
            inv_dir  = 0
            for i in range(len(series) - 1):
                dhp = hp_list[i+1] - hp_list[i]
                dv  = series[i+1] - series[i]
                if dhp == 0 or dv == 0:
                    continue
                if (dhp > 0 and dv > 0) or (dhp < 0 and dv < 0):
                    same_dir += 1
                if (dhp > 0 and dv < 0) or (dhp < 0 and dv > 0):
                    inv_dir += 1

            results.append({
                "off": off,
                "values": series,
                "same_dir": same_dir,
                "inv_dir": inv_dir,
            })

        # Sort: prioritize inverse tracking (red/white life style), then same-dir
        results.sort(
            key=lambda r: (r["inv_dir"], r["same_dir"]),
            reverse=True
        )

        print("===== redscan analysis =====")
        for r in results[:top_n]:
            off = r["off"]
            vs  = r["values"]
            print(
                f"off 0x{off:03X}: {vs}  "
                f"same_dir={r['same_dir']} inv_dir={r['inv_dir']}"
            )
        print("===== end redscan =====")
        print("Tip: A good red-life candidate will:")
        print("- change when HP changes, but not match HP directly")
        print("- often move in the OPPOSITE direction of HP (inv_dir high)")
