# advantage.py
from config import MAX_DIST2

# Known / inferred states on f062
STATE_IDLE_BASE      = 160  # fully neutral
STATE_ENGAGED        = 168  # interactive/able to act/block; also appears early, so gate w/ was_busy
STATE_ATTACK_ACTIVE  = 0    # active hitbox out
STATE_ACTIVE_MOVE    = 32   # startup/active frames (attacking)
STATE_STUN_LOCK      = 8    # defender locked
STATE_IMPACTED       = 40   # hit / impact freeze

# You said "128 only applies to specials" (special end),
# and normals "give the movement flag".
# We need that movement state's numeric value.
STATE_SPECIAL_END    = 128  # attacker special just ended, can act
STATE_MOVEMENT       = 9999 # TODO: replace 9999 with the actual flag_062 value you see for generic movement / walking after normals recover

CONTACT_TIMEOUT_FRAMES = 30


class AdvantageTracker:
    def __init__(self):
        """
        self.pairs[(atk_base, vic_base)] = {
            "active": bool,
            "contact_frame": int|None,
            "last_touch_frame": int|None,

            "armed": False,          # did we confirm real pressure (atk attacking vs vic locked)?

            "atk_was_busy": False,   # has attacker left idle this string?
            "vic_was_busy": False,   # has victim left idle this string?

            "atk_first_free_frame": None,  # first actionable frame for attacker
            "vic_first_free_frame": None,  # first actionable frame for victim

            "done": False,
            "plus_frames": None,
            "finalized_frame": None,
        }
        """
        self.pairs = {}

    def _get_pair_state(self, atk_base, vic_base):
        key = (atk_base, vic_base)
        st = self.pairs.get(key)
        if st is None:
            st = {
                "active": False,
                "contact_frame": None,
                "last_touch_frame": None,

                "armed": False,

                "atk_was_busy": False,
                "vic_was_busy": False,

                "atk_first_free_frame": None,
                "vic_first_free_frame": None,

                "done": False,
                "plus_frames": None,
                "finalized_frame": None,
            }
            self.pairs[key] = st
        return st

    def start_contact(self, atk_base, vic_base, frame_idx):
        """
        On a confirmed hit (HP drop), we hard-reset/initialize that atk->vic pair.
        """
        st = self._get_pair_state(atk_base, vic_base)

        st["active"]           = True
        st["contact_frame"]    = frame_idx
        st["last_touch_frame"] = frame_idx

        st["armed"] = False

        st["atk_was_busy"] = False
        st["vic_was_busy"] = False

        st["atk_first_free_frame"] = None
        st["vic_first_free_frame"] = None

        st["done"]             = False
        st["plus_frames"]      = None
        st["finalized_frame"]  = None

    def _fighter_is_attacking(self, f062):
        """
        Attacker is in startup/active frames if flag_062 is ATTACK_ACTIVE or ACTIVE_MOVE.
        """
        return f062 in (STATE_ATTACK_ACTIVE, STATE_ACTIVE_MOVE)

    def _fighter_is_locked(self, f062):
        """
        Victim is locked if they're in stun, being hit, or still in forced block.
        """
        return f062 in (STATE_STUN_LOCK, STATE_IMPACTED, STATE_ENGAGED)

    def _maybe_arm(self, st, atk_f62, vic_f62):
        """
        We 'arm' this string if we detect real pressure:
          - attacker attacking
          - victim locked
        Once armed, it stays armed for this contact window.
        """
        if st["armed"]:
            return
        if self._fighter_is_attacking(atk_f62) and self._fighter_is_locked(vic_f62):
            st["armed"] = True

    def _mark_busy(self, st, side, f062_val):
        """
        Mark that a fighter has been in a non-idle state during this window.
        That means later ENGAGED/MOVEMENT/etc counts as 'recover' instead of 'neutral wobble.'
        """
        if f062_val is None:
            return
        if f062_val != STATE_IDLE_BASE:
            if side == "atk":
                st["atk_was_busy"] = True
            else:
                st["vic_was_busy"] = True

    def _attacker_recover_priority(self, f062):
        """
        Return a small integer priority if this f062 means
        'attacker can act now', else None.
        Lower number = higher priority.
        """
        if f062 == STATE_SPECIAL_END:
            return 0
        if f062 == STATE_MOVEMENT:
            return 1
        if f062 == STATE_ENGAGED:
            return 2
        if f062 == STATE_IDLE_BASE:
            return 3
        return None

    def _victim_recover_priority(self, f062):
        """
        Same for victim, but ENGAGED is the best signal of 'I'm out of stun,
        I can act', then MOVEMENT, then IDLE_BASE.
        """
        if f062 == STATE_ENGAGED:
            return 0
        if f062 == STATE_MOVEMENT:
            return 1
        if f062 == STATE_IDLE_BASE:
            return 2
        return None

    def _maybe_mark_free(self, st, side, f062_val, frame_idx):
        """
        Stamps first actionable frame for a side if:
          - we're armed
          - they've actually been busy this string
          - we hit a qualifying state according to that side's priority table
          - and we haven't stamped them yet
        """
        if not st["armed"]:
            return
        if f062_val is None:
            return

        if side == "atk":
            if st["atk_first_free_frame"] is not None:
                return
            if not st["atk_was_busy"]:
                return
            prio = self._attacker_recover_priority(f062_val)
            if prio is not None:
                st["atk_first_free_frame"] = frame_idx
        else:
            if st["vic_first_free_frame"] is not None:
                return
            if not st["vic_was_busy"]:
                return
            prio = self._victim_recover_priority(f062_val)
            if prio is not None:
                st["vic_first_free_frame"] = frame_idx

    def _maybe_finalize(self, st, frame_idx):
        """
        Once both attacker and victim have a first_free_frame, lock in plus_frames.
        plus_frames = victim_free - attacker_free
        """
        if st["done"]:
            return
        a_f = st["atk_first_free_frame"]
        v_f = st["vic_first_free_frame"]
        if (a_f is not None) and (v_f is not None):
            st["plus_frames"]     = (v_f - a_f)
            st["done"]            = True
            st["finalized_frame"] = frame_idx

    def update_pair(self, atk_snap, vic_snap, d2_val, frame_idx):
        """
        Called every frame in both directions.
        Keeps the contact window alive, arms it if needed,
        records first actionable frame for each side, finalizes result,
        and times out old windows.
        """
        if atk_snap is None or vic_snap is None:
            return

        atk_b = atk_snap["base"]
        vic_b = vic_snap["base"]
        st = self._get_pair_state(atk_b, vic_b)

        # Are they interacting (close or either busy)?
        close_enough = False
        if d2_val is not None and d2_val != float("inf"):
            close_enough = (d2_val <= MAX_DIST2)

        atk_f62 = atk_snap["f062"]
        vic_f62 = vic_snap["f062"]

        atk_busy_now = (atk_f62 is not None and atk_f62 != STATE_IDLE_BASE)
        vic_busy_now = (vic_f62 is not None and vic_f62 != STATE_IDLE_BASE)

        interacting = close_enough or atk_busy_now or vic_busy_now

        # open or refresh window
        if interacting:
            if (not st["active"]) or st["done"]:
                st["active"]           = True
                st["contact_frame"]    = frame_idx
                st["last_touch_frame"] = frame_idx

                st["armed"] = False

                st["atk_was_busy"] = False
                st["vic_was_busy"] = False

                st["atk_first_free_frame"] = None
                st["vic_first_free_frame"] = None

                st["done"]             = False
                st["plus_frames"]      = None
                st["finalized_frame"]  = None
            else:
                st["last_touch_frame"] = frame_idx

        # if we're active and not done, gather data
        if st["active"] and (not st["done"]):
            # mark that they actually left idle
            self._mark_busy(st, "atk", atk_f62)
            self._mark_busy(st, "vic", vic_f62)

            # arm the interaction if attacker is actually hitting and victim is locked
            self._maybe_arm(st, atk_f62, vic_f62)

            # record first actionable frame for attacker and victim
            self._maybe_mark_free(st, "atk", atk_f62, frame_idx)
            self._maybe_mark_free(st, "vic", vic_f62, frame_idx)

            # finalize if both are stamped
            self._maybe_finalize(st, frame_idx)

        # timeout after inactivity
        if st["active"] and (st["last_touch_frame"] is not None):
            if (frame_idx - st["last_touch_frame"]) > CONTACT_TIMEOUT_FRAMES:
                st["active"] = False

    def get_freshest_final_info(self):
        """
        Return (atk_base, vic_base, plus_frames, finalized_frame) for the
        most recently finalized interaction, or None.
        """
        newest = None
        newest_frame = -1
        for (atk_b, vic_b), st in self.pairs.items():
            if st.get("done") and (st.get("plus_frames") is not None):
                fin_f = st.get("finalized_frame")
                if fin_f is None:
                    continue
                if fin_f > newest_frame:
                    newest_frame = fin_f
                    newest = (atk_b, vic_b, st["plus_frames"], fin_f)
        return newest


ADV_TRACK = AdvantageTracker()
