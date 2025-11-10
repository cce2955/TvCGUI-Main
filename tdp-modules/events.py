# events.py
#
# Shared event log for HUD

event_log: list[str] = []

_MAX_LOG = 200

_last_engagement: tuple[int, int] | None = None
_last_engagement_frame: int = -9999
_ENGAGE_COOLDOWN = 15  # frames


def _push(line: str):
    event_log.append(line)
    if len(event_log) > _MAX_LOG:
        del event_log[:-_MAX_LOG]


def log_text(line: str):
    _push(line)


def log_engaged(attacker_snap: dict, victim_snap: dict, frame_idx: int):
    global _last_engagement, _last_engagement_frame

    atk_base = attacker_snap["base"]
    vic_base = victim_snap["base"]
    key = (atk_base, vic_base)

    if _last_engagement == key and (frame_idx - _last_engagement_frame) < _ENGAGE_COOLDOWN:
        return

    _last_engagement = key
    _last_engagement_frame = frame_idx

    atk_slot = attacker_snap.get("slotname", "???")
    atk_name = attacker_snap.get("name", "???")
    vic_slot = victim_snap.get("slotname", "???")
    vic_name = victim_snap.get("name", "???")

    _push(f"[{frame_idx:05d}] ENGAGED {atk_slot}({atk_name}) -> {vic_slot}({vic_name})")


def log_hit(attacker_snap: dict, victim_snap: dict, damage: int, frame_idx: int):
    atk_slot = attacker_snap.get("slotname", "???")
    atk_name = attacker_snap.get("name", "???")
    vic_slot = victim_snap.get("slotname", "???")
    vic_name = victim_snap.get("name", "???")
    _push(f"[{frame_idx:05d}] HIT {atk_slot}({atk_name}) -> {vic_slot}({vic_name}) dmg:{damage}")


def log_frame_advantage(attacker_snap: dict | None,
                        victim_snap: dict | None,
                        plus_frames: float):
    if attacker_snap is not None:
        atk_slot = attacker_snap.get("slotname", "???")
        atk_name = attacker_snap.get("name", "???")
    else:
        atk_slot = "???"
        atk_name = "???"

    if victim_snap is not None:
        vic_slot = victim_snap.get("slotname", "???")
        vic_name = victim_snap.get("name", "???")
    else:
        vic_slot = "???"
        vic_name = "???"

    _push(f"ADV {atk_slot}({atk_name}) vs {vic_slot}({vic_name}): {plus_frames:+.1f}f")
