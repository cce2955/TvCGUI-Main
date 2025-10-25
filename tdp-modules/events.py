# events.py
# Central hit event log buffer + helper. :contentReference[oaicite:11]{index=11}

event_log = []
MAX_LOG_LINES = 60

def log_hit_line(data):
    """
    Append a formatted 'HIT ...' string into the rolling HUD event_log.
    Keeps most recent MAX_LOG_LINES lines for UI. :contentReference[oaicite:12]{index=12}
    """
    s = (
        f"HIT {data['victim_label']}({data['victim_char']}) "
        f"dmg={data['dmg']} hp:{data['hp_before']}->{data['hp_after']} "
        f"from {data['attacker_label']} "
        f"moveID={data['attacker_id_dec']} '{data['attacker_move']}' "
        f"d2={data['dist2']:.3f}"
    )
    event_log.append(s)
    if len(event_log) > 200:
        # trim down to most recent ~200
        del event_log[0:len(event_log)-200]
