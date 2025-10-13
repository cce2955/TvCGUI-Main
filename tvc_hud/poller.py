import time, threading, queue
from .constants import *
from .memory import hook, RESOLVER, read_fighter_block, best_posy_off, METER_CACHE
from .game import FighterState, name_for_char, dist2

class Poller(threading.Thread):
    """Reads game memory and emits EVT_UPDATE / EVT_HIT events into a queue."""
    def __init__(self, out_q: "queue.Queue"):
        super().__init__(daemon=True)
        self.q = out_q
        self.running = True
        self.fighters = {lab: FighterState(lab, team) for (lab,_,team) in SLOTS}
        self.slot_map = {lab: addr for (lab,addr,_) in SLOTS}

    def run(self):
        hook()
        while self.running:
            # resolve bases
            for lab, slot_addr in self.slot_map.items():
                base, changed = RESOLVER.resolve_base(slot_addr)
                f = self.fighters[lab]
                if base and base != f.base:
                    f.base = base
                    f.posy_off = best_posy_off(base)
                    METER_CACHE.drop(base)
                    f.prev_last_hit = None
                    f.last_hp = None

            # read + detect hits
            for lab, f in self.fighters.items():
                if not f.base: continue
                blk = read_fighter_block(f.base, f.posy_off, want_meter=lab.endswith("C1"))
                if not blk: continue

                prev_last_hit = f.prev_last_hit
                prev_hp = f.last_hp
                was_hit = False
                dmg = None
                if blk["last_hit"] and blk["last_hit"] != prev_last_hit:
                    was_hit = True; dmg = blk["last_hit"]
                elif prev_hp is not None and blk["cur"] < prev_hp:
                    was_hit = True; dmg = prev_hp - blk["cur"]

                # commit
                f.char_id = blk["char_id"]; f.name = name_for_char(blk["char_id"])
                f.max = blk["max"]; f.cur = blk["cur"]; f.aux = blk["aux"]
                f.x = blk["x"]; f.y = blk["y"]; f.meter = blk["meter"]
                f.last_hit = blk["last_hit"]; f.prev_last_hit = blk["last_hit"]
                f.last_hp = blk["cur"]

                if was_hit and dmg and dmg > 0:
                    opps = [o for o in self.fighters.values() if o.team != f.team and o.base]
                    if opps:
                        attacker = min(opps, key=lambda o: dist2(f, o))
                        self.q.put((EVT_HIT, {
                            "ts": time.time(),
                            "victim": f,
                            "attacker": attacker,
                            "dmg": int(dmg),
                            "hp_from": int(prev_hp if prev_hp is not None else f.cur + dmg),
                            "hp_to": int(f.cur),
                            "dist2": dist2(f, attacker),
                        }))

            # snapshot for UI
            snap = {k: {
                "name": v.name, "team": v.team, "max": v.max, "cur": v.cur,
                "meter": v.meter, "x": v.x, "y": v.y, "last_hit": v.last_hit
            } for k,v in self.fighters.items()}
            self.q.put((EVT_UPDATE, snap))
            time.sleep(POLL_DT)

    def stop(self): self.running = False
