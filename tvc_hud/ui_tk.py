# tvc_hud/ui_tk.py
import tkinter as tk
from tkinter import ttk
import queue, threading
from .constants import EVT_UPDATE, EVT_HIT
from .memory import hook

class HUD(tk.Tk):
    def __init__(self, poller_cls, *poller_args, **poller_kwargs):
        super().__init__()
        self.title("TvC HUD")
        self.geometry("1320x760")
        self.configure(bg="#0e0e10")

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#1a1b1e")
        style.configure("TLabel", background="#1a1b1e", foreground="#d0d0d0")
        style.configure("Title.TLabel", font=("Segoe UI", 12, "bold"), foreground="#ffffff")
        style.configure("HP.TLabel", font=("Consolas", 11, "bold"))
        style.configure("Mono.TLabel", font=("Consolas", 10))

        root = ttk.Frame(self, padding=8); root.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1); self.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1); root.rowconfigure(1, weight=0)
        root.columnconfigure(0, weight=1)

        # ----- top grid (2x2 panels) -----
        panels = ttk.Frame(root); panels.grid(row=0, column=0, sticky="nsew")
        for c in (0,1): panels.columnconfigure(c, weight=1)
        for r in (0,1): panels.rowconfigure(r, weight=1)
        self.panels = {}

        def make_panel(parent, title, col, row, accent):
            f = ttk.Frame(parent, padding=10)
            f.grid(column=col, row=row, sticky="nsew", padx=6, pady=6)
            top   = ttk.Label(f, text=title, style="Title.TLabel"); top.pack(anchor="w")
            ptr   = ttk.Label(f, text="Ptr: --", style="Mono.TLabel"); ptr.pack(anchor="w")
            hp    = ttk.Label(f, text="HP: --/-- (---.-%)", style="HP.TLabel"); hp.pack(anchor="w", pady=(4,0))
            meter = ttk.Label(f, text="Meter: --", style="Mono.TLabel"); meter.pack(anchor="w")
            pos   = ttk.Label(f, text="Pos: X:--  Y:--", style="Mono.TLabel"); pos.pack(anchor="w")
            last  = ttk.Label(f, text="LastDmg: --", style="Mono.TLabel"); last.pack(anchor="w")
            ttk.Label(f, text="Last Attacks:", style="Mono.TLabel").pack(anchor="w", pady=(6,0))
            atk_list = tk.Text(f, height=3, bg="#101114", fg="#d0d0d0", font=("Consolas", 9),
                               borderwidth=0, highlightthickness=0)
            atk_list.configure(state="disabled")
            atk_list.pack(fill="x")
            bar   = tk.Frame(f, height=3, bg=accent); bar.pack(fill="x", pady=(8,0))
            return {"frame": f, "ptr": ptr,"hp": hp, "meter": meter, "pos": pos, "last": last,
                    "title": top, "atk_list": atk_list}

        self.panels["P1-C1"] = make_panel(panels, "P1-C1", 0, 0, "#3aa0ff")
        self.panels["P1-C2"] = make_panel(panels, "P1-C2", 0, 1, "#3aa0ff")
        self.panels["P2-C1"] = make_panel(panels, "P2-C1", 1, 0, "#ff4d4f")
        self.panels["P2-C2"] = make_panel(panels, "P2-C2", 1, 1, "#ff4d4f")

        # ----- bottom tabs (Hits / Combos) -----
        tabs = ttk.Notebook(root); tabs.grid(row=1, column=0, sticky="nsew", pady=(6,0))
        hits_frame = ttk.Frame(tabs); combos_frame = ttk.Frame(tabs)
        tabs.add(hits_frame, text="Recent Hits"); tabs.add(combos_frame, text="Combos")
        for fr in (hits_frame, combos_frame):
            fr.columnconfigure(0, weight=1); fr.rowconfigure(0, weight=1)

        self.hits = tk.Text(hits_frame, height=10, bg="#101114", fg="#d0d0d0",
                            font=("Consolas", 9), borderwidth=0, highlightthickness=0)
        self.hits.configure(state="disabled"); self.hits.grid(row=0, column=0, sticky="nsew")
        hs = ttk.Scrollbar(hits_frame, orient="vertical", command=self.hits.yview)
        self.hits.configure(yscrollcommand=hs.set); hs.grid(row=0, column=1, sticky="ns")

        self.combos = tk.Text(combos_frame, height=10, bg="#101114", fg="#d0d0d0",
                              font=("Consolas", 9), borderwidth=0, highlightthickness=0)
        self.combos.configure(state="disabled"); self.combos.grid(row=0, column=0, sticky="nsew")
        cs = ttk.Scrollbar(combos_frame, orient="vertical", command=self.combos.yview)
        self.combos.configure(yscrollcommand=cs.set); cs.grid(row=0, column=1, sticky="ns")

        # poller thread
        self.q = queue.Queue()
        self.poller = poller_cls(self.q, *poller_args, **poller_kwargs)
        self.after(10, self._start)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _start(self):
        threading.Thread(target=hook, daemon=True).start()
        self.poller.start()
        self.after(50, self._drain_queue)

    def _drain_queue(self):
        import queue as _q
        try:
            while True:
                evt, payload = self.q.get_nowait()
                if evt == EVT_UPDATE: self._apply_snapshot(payload)
                elif evt == EVT_HIT: self._append_hit(payload)
                elif evt == "combo_summary": self._append_combo(payload)
        except _q.Empty:
            pass
        self.after(50, self._drain_queue)

    def _panel_set_attacks(self, panel, attacks):
        panel["atk_list"].configure(state="normal")
        panel["atk_list"].delete("1.0","end")
        if attacks:
            for i, a in enumerate(attacks, 1):
                panel["atk_list"].insert("end", f"{i}. {a}\n")
        panel["atk_list"].configure(state="disabled")

    def _apply_snapshot(self, snap):
        for key, panel in self.panels.items():
            data = snap.get(key)
            if not data:
                panel["title"].configure(text=f"{key} — (waiting)")
                panel["ptr"].configure(text="Ptr: --")
                panel["hp"].configure(text="HP: --/-- (---.-%)", foreground="#9aa0a6")
                panel["meter"].configure(text="Meter: --")
                panel["pos"].configure(text="Pos: X:--  Y:--")
                panel["last"].configure(text="LastDmg: --")
                self._panel_set_attacks(panel, [])
                continue
            name = data["name"]; cur, mx = data["cur"], data["max"]
            pct = (cur / mx * 100.0) if mx else 0.0
            panel["title"].configure(text=f"{key} — {name}")
            panel["ptr"].configure(text=f"Ptr: {data['ptr']}")
            color = "#50fa7b" if mx and pct > 66 else ("#f1fa8c" if mx and pct > 33 else "#ff5555")
            panel["hp"].configure(text=f"HP: {cur}/{mx} ({pct:5.1f}%)", foreground=color)
            m = data["meter"]; panel["meter"].configure(text=f"Meter: {m if m is not None else '--'}")
            x = "--" if data["x"] is None else f"{data['x']:.3f}"
            y = "--" if data["y"] is None else f"{data['y']:.3f}"
            panel["pos"].configure(text=f"Pos: X:{x}  Y:{y}")
            lh = data["last_hit"]; panel["last"].configure(text=f"LastDmg: {lh if lh else '--'}")
            self._panel_set_attacks(panel, data.get("last_attacks") or [])

    def _append_hit(self, h):
        ts = h["ts"]; vic = h["victim"]; atk = h["attacker"]
        name = h.get("atk_name") or "--"
        atk_id = h.get("atk_id"); atk_sub=h.get("atk_sub")
        line = (f"[{int(ts)}] HIT  victim={vic['label']}({vic['name']:<16}) "
                f"ptr={vic['ptr']}  dmg={h['dmg']:4d}  hp:{h['hp_from']}->{h['hp_to']}  "
                f"attacker≈{atk['label']}({atk['name']}) ptr={atk['ptr']}  dist2={h['dist2']:.3f}  "
                f"atk_id={(hex(atk_id) if isinstance(atk_id,int) else '--')} sub={(hex(atk_sub) if isinstance(atk_sub,int) else '--')} "
                f"name={name}\n")
        self.hits.configure(state="normal")
        self.hits.insert("end", line)
        if int(self.hits.index('end-1c').split('.')[0]) > 1000:
            self.hits.delete("1.0", "100.0")
        self.hits.see("end")
        self.hits.configure(state="disabled")

    def _append_combo(self, s):
        line = (f"[{s['t0']:.3f}->{s['t1']:.3f}] COMBO {s['attacker_label']}({s['attacker_name']}) "
                f"→ {s['victim_label']}({s['victim_name']})  hits={s['hits']}  total={s['total']}  "
                f"hp:{s['hp_start']}→{s['hp_end']}  team={s['team_guess'] or '--'}\n")
        self.combos.configure(state="normal")
        self.combos.insert("end", line)
        if int(self.combos.index('end-1c').split('.')[0]) > 1000:
            self.combos.delete("1.0", "100.0")
        self.combos.see("end")
        self.combos.configure(state="disabled")

    def on_close(self):
        try: self.poller.stop()
        except Exception: pass
        self.destroy()
