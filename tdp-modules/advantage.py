# advantage.py
# Simple frame advantage tracker based on move_id transitions

CONTACT_TIMEOUT_FRAMES = 60  # how long to wait before giving up on a calculation


class AdvantageTracker:
    def __init__(self):
        """
        Track frame advantage by watching move_id changes.
        
        self.pairs[(atk_base, vic_base)] = {
            "active": bool,              # is this interaction being tracked?
            "contact_frame": int,        # frame when hit occurred
            "last_update_frame": int,    # last frame we saw activity
            
            "atk_move_id": int,          # attacker's move_id at contact
            "vic_move_id": int,          # victim's move_id at contact
            
            "atk_recover_frame": None,   # frame attacker's move_id changed
            "vic_recover_frame": None,   # frame victim's move_id changed
            
            "done": bool,                # calculation complete?
            "plus_frames": None,         # final advantage
            "finalized_frame": None,     # when we finalized
        }
        """
        self.pairs = {}

    def start_contact(self, atk_base, vic_base, frame_idx, atk_move_id, vic_move_id):
        """
        Called when damage is detected OR when block is detected.
        Records the initial move_ids and starts tracking.
        """
        if atk_move_id is None or vic_move_id is None:
            return  # can't track without move_ids
        
        key = (atk_base, vic_base)
        
        # Don't restart if we're already tracking this interaction
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
            "reported": False,  # has this result been shown to user?
        }
        #print(f"[ADV] Started tracking at frame {frame_idx}: atk_move={atk_move_id}, vic_move={vic_move_id}")

    def update_pair(self, atk_base, vic_base, frame_idx, atk_move_id, vic_move_id):
        """
        Called every frame to check if move_ids have changed (recovery).
        """
        key = (atk_base, vic_base)
        
        if key not in self.pairs:
            return
        
        state = self.pairs[key]
        
        # Skip if already done
        if state["done"]:
            return
        
        # Skip if not active
        if not state["active"]:
            return
        
        # Update activity timestamp
        state["last_update_frame"] = frame_idx
        
        # Check if attacker recovered (move_id changed from original)
        if state["atk_recover_frame"] is None:
            if atk_move_id is not None and atk_move_id != state["atk_move_id"]:
                state["atk_recover_frame"] = frame_idx
                #print(f"[ADV] Attacker recovered at frame {frame_idx}: {state['atk_move_id']} -> {atk_move_id}")
        
        # Check if victim recovered (move_id changed from original)
        if state["vic_recover_frame"] is None:
            if vic_move_id is not None and vic_move_id != state["vic_move_id"]:
                state["vic_recover_frame"] = frame_idx
                #print(f"[ADV] Victim recovered at frame {frame_idx}: {state['vic_move_id']} -> {vic_move_id}")
        
        # Finalize if both recovered
        if state["atk_recover_frame"] is not None and state["vic_recover_frame"] is not None:
            state["plus_frames"] = state["vic_recover_frame"] - state["atk_recover_frame"]
            state["done"] = True
            state["finalized_frame"] = frame_idx
            state["active"] = False
            #print(f"[ADV] Finalized at frame {frame_idx}: {state['plus_frames']:+.1f}f advantage")
        
        # Timeout if taking too long
        if frame_idx - state["contact_frame"] > CONTACT_TIMEOUT_FRAMES:
            state["active"] = False

    def get_freshest_final_info(self):
        """
        Return (atk_base, vic_base, plus_frames, finalized_frame) 
        for the most recently completed calculation, or None.
        After returning, marks that result as "reported" so it won't show again.
        """
        newest = None
        newest_frame = -1
        newest_key = None
        
        for (atk_b, vic_b), state in self.pairs.items():
            # Skip if already reported
            if state.get("reported"):
                continue
            
            if state["done"] and state["plus_frames"] is not None:
                fin_frame = state.get("finalized_frame")
                if fin_frame is None:
                    continue
                if fin_frame > newest_frame:
                    newest_frame = fin_frame
                    newest = (atk_b, vic_b, state["plus_frames"], fin_frame)
                    newest_key = (atk_b, vic_b)
        
        # Mark as reported so we don't show it again
        if newest_key:
            self.pairs[newest_key]["reported"] = True
        
        return newest


# Global tracker instance
ADV_TRACK = AdvantageTracker()