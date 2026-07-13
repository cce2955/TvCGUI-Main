# advantage.py
#
# Lightweight frame advantage tracker.
#
# The idea is:
#   - When a hit/block is detected, the module record both players' current move IDs.
#   - On each subsequent frame, the module watch for those move IDs to change.
#   - The first change for each side is treated as "recovery".
#   - Frame advantage = victim_recover_frame - attacker_recover_frame.
#
# Positive values mean the victim recovers first (attacker is negative),
# negative values mean the attacker recovers first (attacker is plus).

# How many frames keep watching an interaction before giving up.
CONTACT_TIMEOUT_FRAMES = 60  # ~1 second at 60 FPS


class AdvantageTracker:
    def __init__(self):
        '\n        Track frame advantage by watching move_id transitions for pairs\n        of characters, keyed by their base addresses.\n\n        self.pairs[(atk_base, vic_base)] = {\n            "active": bool,              # currently tracking this contact?\n            "contact_frame": int,        # frame when the hit/block happened\n            "last_update_frame": int,    # last frame the module saw this pair\n\n            "atk_move_id": int,          # attacker\'s move_id at contact\n            "vic_move_id": int,          # victim\'s move_id at contact\n\n            "atk_recover_frame": None,   # when attacker left that move_id\n            "vic_recover_frame": None,   # when victim left that move_id\n\n            "done": bool,                # calculation completed?\n            "plus_frames": None,         # victim_recover - attacker_recover\n            "finalized_frame": None,     # frame index when the module finished\n            "reported": bool,            # has this result already been shown?\n        }\n        '
        # (atk_base, vic_base) -> state dict (see above)
        self.pairs = {}

    def start_contact(self, atk_base, vic_base, frame_idx, atk_move_id, vic_move_id):
        '\n        Begin tracking an interaction between attacker and victim.\n\n        Called at the moment the module detect a hit or block. The module record the current\n        move IDs for both parties and treat any subsequent change as "recovery".\n        '
        if atk_move_id is None or vic_move_id is None:
            # If do not know the move IDs, the module has nothing meaningful to track.
            return

        key = (atk_base, vic_base)

        # If the module is already watching this exact pair and it's active, don't reset.
        # This avoids constantly restarting during multi-hit strings.
        if key in self.pairs and self.pairs[key]["active"]:
            return

        self.pairs[key] = {
            "active": True,
            "contact_frame": frame_idx,
            "last_update_frame": frame_idx,

            "atk_move_id": atk_move_id,
            "vic_move_id": vic_move_id,

            "atk_recover_frame": None,
            "vic_recover_frame": None,

            "done": False,
            "plus_frames": None,
            "finalized_frame": None,
            "reported": False,  # set to True once surfaced to the HUD
        }
        # Debug logging left commented for on-demand troubleshooting.
        # print(f"[ADV] Start @ {frame_idx}: atk_move={atk_move_id}, vic_move={vic_move_id}")

    def update_pair(self, atk_base, vic_base, frame_idx, atk_move_id, vic_move_id):
        "\n        Advance the state machine for a given attacker/victim pair.\n\n        This should be called every frame while both characters are on-screen.\n        Look for the first time each side's move_id changes from the value\n        captured at contact, and treat those moments as recovery.\n        "
        key = (atk_base, vic_base)

        if key not in self.pairs:
            return

        state = self.pairs[key]

        # Nothing to do once the module has finished computing plus_frames.
        if state["done"]:
            return

        if not state["active"]:
            return

        # Keep track of the last frame this pair was updated, mostly for
        # debugging / future heuristics.
        state["last_update_frame"] = frame_idx

        # Attacker recovery: first time the move_id changes away from contact.
        if state["atk_recover_frame"] is None:
            if atk_move_id is not None and atk_move_id != state["atk_move_id"]:
                state["atk_recover_frame"] = frame_idx
                # print(f"[ADV] Attacker recovered @ {frame_idx}: {state['atk_move_id']} -> {atk_move_id}")

        # Victim recovery: same idea as above.
        if state["vic_recover_frame"] is None:
            if vic_move_id is not None and vic_move_id != state["vic_move_id"]:
                state["vic_recover_frame"] = frame_idx
                # print(f"[ADV] Victim recovered @ {frame_idx}: {state['vic_move_id']} -> {vic_move_id}")

        # Once both sides have recovered, the module can compute the actual advantage.
        if state["atk_recover_frame"] is not None and state["vic_recover_frame"] is not None:
            state["plus_frames"] = state["vic_recover_frame"] - state["atk_recover_frame"]
            state["done"] = True
            state["finalized_frame"] = frame_idx
            state["active"] = False
            # print(f"[ADV] Finalized @ {frame_idx}: {state['plus_frames']:+.1f}f")

        # Give up if the whole sequence has dragged on for too long.
        if frame_idx - state["contact_frame"] > CONTACT_TIMEOUT_FRAMES:
            state["active"] = False

    def get_freshest_final_info(self):
        """
        Return the most recent completed result that hasn't been reported yet.

        Returns:
            (atk_base, vic_base, plus_frames, finalized_frame) or None

        Once a result is returned, it's marked as "reported" so it will only
        be surfaced once to the caller.
        """
        newest = None
        newest_frame = -1
        newest_key = None

        for (atk_b, vic_b), state in self.pairs.items():
            # Skip entries the module has already emitted to the HUD/log.
            if state.get("reported"):
                continue

            if state["done"] and state["plus_frames"] is not None:
                fin_frame = state.get("finalized_frame")
                if fin_frame is None:
                    continue
                # Keep the result finalized most recently in time.
                if fin_frame > newest_frame:
                    newest_frame = fin_frame
                    newest = (atk_b, vic_b, state["plus_frames"], fin_frame)
                    newest_key = (atk_b, vic_b)

        # Mark the chosen entry as reported so do not repeat it.
        if newest_key:
            self.pairs[newest_key]["reported"] = True

        return newest


# Single shared tracker used by the HUD.
ADV_TRACK = AdvantageTracker()
