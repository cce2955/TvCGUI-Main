# events.py
#
# Shared lightweight event log for the HUD.
# The HUD polls this module to render recent combat events
# such as engagements, hits, and calculated frame advantage.

event_log = []
MAX_EVENTS = 120


def _push(msg: str):
    """
    Append a message to the circular event log, trimming
    old entries when MAX_EVENTS is exceeded.
    """
    event_log.append(msg)
    if len(event_log) > MAX_EVENTS:
        del event_log[0 : len(event_log) - MAX_EVENTS]


def log_engaged(attacker_snap: dict, victim_snap: dict, frame_idx: int):
    """
    Log that two fighters have entered engagement range / collision state.

    attacker_snap / victim_snap:
        Shallow fighter snapshots from the HUD loop.
    frame_idx:
        The global frame counter at the moment of the event.
    """
    a_slot = attacker_snap.get("slotname", "?")
    a_name = attacker_snap.get("name", "?")
    v_slot = victim_snap.get("slotname", "?")
    v_name = victim_snap.get("name", "?")

    _push(f"[{frame_idx}] ENGAGED: {a_slot}({a_name}) → {v_slot}({v_name})")


def log_hit(
    attacker_snap: dict,
    victim_snap: dict,
    dmg: int,
    frame_idx: int,
    atk_move_label: str = None,
    atk_move_id: int = None,
):
    """
    Log a hit event with optional move information.

    dmg:
        Damage dealt by the hit.
    atk_move_label:
        Friendly string label for the move if known.
    atk_move_id:
        Raw animation ID when no label is available.
    """
    a_slot = attacker_snap.get("slotname", "?")
    a_name = attacker_snap.get("name", "?")
    v_slot = victim_snap.get("slotname", "?")
    v_name = victim_snap.get("name", "?")

    if atk_move_label:
        move_part = f" [{atk_move_label}]"
    elif atk_move_id is not None:
        move_part = f" [anim_{atk_move_id:02X}]"
    else:
        move_part = ""

    _push(
        f"[{frame_idx}] HIT: {a_slot}({a_name}){move_part} → "
        f"{v_slot}({v_name}) dmg:{dmg}"
    )


def log_frame_advantage(attacker_snap: dict, victim_snap: dict, plusf: float):
    """
    Log calculated frame advantage after block/hit interactions.

    plusf:
        Advantage in frames (positive = attacker advantage, negative = defender).
    """
    a_slot = attacker_snap.get("slotname", "?")
    a_name = attacker_snap.get("name", "?")
    v_slot = victim_snap.get("slotname", "?")
    v_name = victim_snap.get("name", "?")

    _push(
        f"FRAME ADV: {a_slot}({a_name}) vs {v_slot}({v_name}) = {plusf:+.1f}f"
    )
