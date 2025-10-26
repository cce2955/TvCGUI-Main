# events.py
import time

event_log = []
MAX_LOG_LINES = 100

def _trim():
    if len(event_log) > 300:
        del event_log[0:len(event_log)-300]

def log_hit_line(data):
    s = (
        f"HIT {data['victim_label']}({data['victim_char']}) "
        f"dmg={data['dmg']} hp:{data['hp_before']}->{data['hp_after']} "
        f"from {data['attacker_label']} "
        f"moveID={data['attacker_id_dec']} '{data['attacker_move']}' "
        f"d2={data['dist2']:.3f}"
    )
    event_log.append(s)
    _trim()

def log_frame_advantage(atk_snap, vic_snap, plusf):
    ts = time.time()
    if atk_snap and vic_snap:
        s = (
            f"[ADV {ts:.2f}] "
            f"{atk_snap['slotname']}({atk_snap['name']}) "
            f"vs {vic_snap['slotname']}({vic_snap['name']}): "
            f"{plusf:+.1f}f"
        )
    else:
        s = f"[ADV {ts:.2f}] Frame adv {plusf:+.1f}f"
    event_log.append(s)
    _trim()
