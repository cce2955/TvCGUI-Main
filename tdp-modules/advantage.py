# advantage.py
# Tracks frame advantage windows between attacker/victim pairs. :contentReference[oaicite:10]{index=10}

from config import MAX_DIST2

class AdvantageTracker:
    """
    We track each attacker/victim pair.
    - When hit happens, call start_contact(atk_base, vic_base, frame_idx)
    - Each frame, call update_pair(atk_snap, vic_snap, d2_val, frame_idx)
    - get_latest_adv() returns plus_frames if finalized.
    plus_frames = vic_idle_frame - atk_idle_frame
      >0 means attacker recovered first (attacker has the turn / "+" frames)
      <0 means victim actually recovered first (victim is plus)
    """

    def __init__(self):
        # { (atk_base, vic_base): state }
        self.pairs = {}

    def _get_pair_state(self, atk_base, vic_base):
        key = (atk_base, vic_base)
        st = self.pairs.get(key)
        if st is None:
            st = {
                "active": False,
                "contact_frame": None,
                "last_touch_frame": None,
                "atk_idle_frame": None,
                "vic_idle_frame": None,
                "done": False,
                "plus_frames": None,
            }
            self.pairs[key] = st
        return st

    def start_contact(self, atk_base, vic_base, frame_idx):
        st = self._get_pair_state(atk_base, vic_base)

        # new or reset interaction
        if (not st["active"]) or st["done"]:
            st["active"]            = True
            st["contact_frame"]     = frame_idx
            st["last_touch_frame"]  = frame_idx
            st["atk_idle_frame"]    = None
            st["vic_idle_frame"]    = None
            st["done"]              = False
            st["plus_frames"]       = None
        else:
            # refresh
            st["last_touch_frame"]  = frame_idx

    def get_latest_adv(self, atk_base, vic_base):
        st = self.pairs.get((atk_base, vic_base))
        if not st:
            return None
        return st.get("plus_frames")

    def update_pair(self, atk_snap, vic_snap, d2_val, frame_idx):
        if atk_snap is None or vic_snap is None:
            return

        atk_b = atk_snap["base"]
        vic_b = vic_snap["base"]

        st = self._get_pair_state(atk_b, vic_b)

        # 1. see if they're "interacting"
        close_enough = False
        if d2_val is not None and d2_val != float("inf"):
            close_enough = (d2_val <= MAX_DIST2)

        atk_f62 = atk_snap["f062"]
        vic_f62 = vic_snap["f062"]

        atk_busy = (atk_f62 is not None and atk_f62 != 160)
        vic_busy = (vic_f62 is not None and vic_f62 != 160)

        interacting = close_enough or atk_busy or vic_busy

        if interacting:
            if (not st["active"]) or st["done"]:
                st["active"]            = True
                st["contact_frame"]     = frame_idx
                st["last_touch_frame"]  = frame_idx
                st["atk_idle_frame"]    = None
                st["vic_idle_frame"]    = None
                st["done"]              = False
                st["plus_frames"]       = None
            else:
                st["last_touch_frame"]  = frame_idx

        # 2. record first idle frames (f062 == 160 == IDLE_BASE)
        if st["active"] and (not st["done"]):
            if st["atk_idle_frame"] is None and atk_f62 == 160:
                st["atk_idle_frame"] = frame_idx
            if st["vic_idle_frame"] is None and vic_f62 == 160:
                st["vic_idle_frame"] = frame_idx

            if (st["atk_idle_frame"] is not None) and (st["vic_idle_frame"] is not None):
                st["plus_frames"] = (st["vic_idle_frame"] - st["atk_idle_frame"])
                st["done"] = True

        # 3. timeout old interactions
        if st["active"] and st["last_touch_frame"] is not None:
            if (frame_idx - st["last_touch_frame"]) > 30:
                st["active"] = False

ADV_TRACK = AdvantageTracker()
