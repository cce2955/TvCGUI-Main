# events.py

# simple shared log the HUD reads
event_log = []
MAX_EVENTS = 120


def _push(msg: str):
    event_log.append(msg)
    if len(event_log) > MAX_EVENTS:
        del event_log[0 : len(event_log) - MAX_EVENTS]


def log_engaged(attacker_snap: dict, victim_snap: dict, frame_idx: int):
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
    a_slot = attacker_snap.get("slotname", "?")
    a_name = attacker_snap.get("name", "?")
    v_slot = victim_snap.get("slotname", "?")
    v_name = victim_snap.get("name", "?")

    move_part = ""
    if atk_move_label:
        move_part = f" [{atk_move_label}]"
    elif atk_move_id is not None:
        move_part = f" [anim_{atk_move_id:02X}]"

    _push(
        f"[{frame_idx}] HIT: {a_slot}({a_name}){move_part} → {v_slot}({v_name}) dmg:{dmg}"
    )


def log_frame_advantage(attacker_snap: dict, victim_snap: dict, plusf: float):
    a_slot = attacker_snap.get("slotname", "?")
    a_name = attacker_snap.get("name", "?")
    v_slot = victim_snap.get("slotname", "?")
    v_name = victim_snap.get("name", "?")
    _push(
        f"FRAME ADV: {a_slot}({a_name}) vs {v_slot}({v_name}) = {plusf:+.1f}f"
    )
