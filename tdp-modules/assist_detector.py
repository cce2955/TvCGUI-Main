# assist_detector.py
#
# Assist animation classifier.
#
# This module provides a simple state machine that watches each character’s
# current animation ID and infers whether they are currently performing an
# assist action. Since assist animations vary slightly per character, the
# system is intentionally loose: it classifies known fly-in and hit frames,
# then falls back to a short “recover → idle” decay so the UI doesn’t flicker.


from collections import defaultdict

# Known animation IDs for the incoming assist jump.
# These values were collected empirically and are not guaranteed to be
# exhaustive; characters with unusual assist entries may require per-char sets.
ASSIST_FLYIN_IDS = {310, 311, 312, 304, 305, 306}

# Placeholder: Assist attack portions differ more across the roster.
# Populate this set once those IDs are fully mapped.
ASSIST_ATTACK_IDS = {...}


class AssistState:
    """
    Per-slot assist state container.

    We track:
        is_assisting : whether the slot is currently in any assist phase
        phase        : "flyin", "attack", "recover", or None
        last_anim    : last seen animation ID (for debugging/inspection)
    """
    def __init__(self):
        self.is_assisting = False
        self.phase = None
        self.last_anim = None


# Slotname → AssistState
# Using defaultdict avoids boilerplate checks during updates.
_ASSIST_BY_SLOT = defaultdict(AssistState)


def update_assist_for_snap(snap):
    """
    Update the assist state for the fighter described by `snap`.

    Expected keys in `snap`:
        snap["slotname"]   – identifier for the HUD slot ("P1-C1", etc.)
        snap["attA"]       – primary current animation ID
        snap["id"]         – character ID (optional, only needed if doing
                              per-character assist ID sets later)

    The update logic is intentionally conservative:
    - Enter "flyin" or "attack" when matching known IDs.
    - When the anim no longer matches, walk through a one-step "recover" phase.
    - This prevents rapid flicker if the game oscillates between neutral
      micro-states during assist end frames.
    """
    slot = snap.get("slotname")
    if not slot:
        return

    st = _ASSIST_BY_SLOT[slot]

    # The assist animation usually lives in attA, but some characters use attB.
    # For now we read attA only; refine this if patterns emerge.
    anim = snap.get("attA")
    st.last_anim = anim

    # Hard classification based on curated sets.
    if anim in ASSIST_FLYIN_IDS:
        st.is_assisting = True
        st.phase = "flyin"

    elif anim in ASSIST_ATTACK_IDS:
        st.is_assisting = True
        st.phase = "attack"

    else:
        # No recognized assist animation this frame; walk the state downward.
        # If we were in an assist phase last frame, transition to a brief
        # "recover" state. Next frame, drop back to idle.
        if st.is_assisting and st.phase in ("flyin", "attack"):
            st.phase = "recover"

        elif st.phase == "recover":
            st.is_assisting = False
            st.phase = None


def get_assist_state(slotname):
    """Return the AssistState object for the given slot name, if any."""
    return _ASSIST_BY_SLOT.get(slotname)
