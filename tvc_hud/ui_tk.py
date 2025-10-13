import tkinter as tk
from tkinter import ttk
import queue, threading
from .constants import EVT_UPDATE, EVT_HIT
from .memory import hook

class HUD(tk.Tk):
    def __init__(self, poller_cls, *poller_args, **poller_kwargs):
        super().__init__()
        self.title("TvC HUD")
        self.geometry("1200x640")
        self.configure(bg="#0e0e10")

        # Style
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#1a1b1e")
        style.configure("TLabel", background="#1a1b1e", foreground="#d0d0d0")
        style.configure("Title.TLabel", font=("Segoe UI", 12, "bold"), foreground="#ffffff")
        style.configure("HP.TLabel", font=("Consolas", 11, "bold"))
        style.configure("Mono.TLabel", font=("Consolas", 10))

        # Root grid
        root = ttk.Frame(self, padding=8)
        root.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1); self.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1); root.rowconfigure(1, weight=0)
        root.columnconfigure(0, weight=1)

        # Panels grid (2x2)
        panels = ttk.Frame(root); panels.grid(row=0, column=0, sticky="nsew")
        for c in (0,1): panels.columnconfigure(c, weight=1)
        for r in (0,1): panels.rowconfigure(r, weight=1)
        self.panels = {}

        def make_panel(parent, title, col, row, accent):
            f = ttk.Frame(parent, padding=10)
            f.grid(column=col, row=row, sticky="nsew", padx=6, pady=6)
            top   = ttk.Label(f, text=title, style="Title.TLabel"); top.pack(anchor="w")
            hp    = ttk.Label(f, text="HP: --/-- (---.-%)", style="HP.TLabel"); hp.pack(anchor="w", pady=(6,0))
            meter = ttk.Label(f, text="Meter: --", style="Mono.TLabel"); meter.pack(anchor="w")
            pos   = ttk.Label(f, text="Pos: X:--  Y:--", style="Mono.TLabel"); pos.pack(anchor="w")
            last  = ttk.Label(f, text="LastDmg: --", style="Mono.TLabel"); last.pack(anchor="w")
            bar   = tk.Frame(f, height=3, bg=accent); bar.pack(fill="x", pady=(8,0))
            return {"frame": f, "hp": hp, "meter": meter, "pos": pos, "last": last, "title": top}

        self.panels["P1-C1"] = make_panel(panels, "P1-C1", 0, 0, "#3aa0ff")
        self.panels["P1-C2"] = make_panel(panels, "P1-C2", 0, 1, "#3aa0ff")
        self.panels["P2-C1"] = make_panel(panels, "P2-C1", 1, 0, "#ff4d4f")
        self.panels["P2-C2"] = make_panel(panels, "P2-C2", 1, 1, "#ff4d4f")

        # Recent Hits
        log_frame = ttk.Frame(root, padding=(2,8,2,2))
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        ttk.Label(log_frame, text="Recent Hits", style="Title.TLabel").grid(row=0, column=0, sticky="w", padx=6, pady=(0,4))
        inner = ttk.Frame(log_frame); inner.grid(row=1, column=0, sticky="nsew")
        log_frame.rowconfigure(1, weight=1)
        inner.columnconfigure(0, weight=1); inner.rowconfigure(0, weight=1)

        yscroll = ttk.Scrollbar(inner, orient="vertical")
        self.log = tk.Text(inner, height=9, yscrollcommand=yscroll.set,
                           bg="#101114", fg="#d0d0d0", insertbackground="#d0d0d0",
                           font=("Consolas", 9), borderwidth=0, highlightthickness=0)
        yscroll.config(command=self.log.yview)
        self.log.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(state="disabled")

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
        except _q.Empty:
            pass
        self.after(50, self._drain_queue)

    def _apply_snapshot(self, snap):
        for key, panel in self.panels.items():
            data = snap.get(key)
            if not data:
                panel["title"].configure(text=f"{key} — (waiting)")
                panel["hp"].configure(text="HP: --/-- (---.-%)", foreground="#9aa0a6")
                panel["meter"].configure(text="Meter: --")
                panel["pos"].configure(text="Pos: X:--  Y:--")
                panel["last"].configure(text="LastDmg: --")
                continue
            name = data["name"]; cur, mx = data["cur"], data["max"]
            pct = (cur / mx * 100.0) if mx else 0.0
            panel["title"].configure(text=f"{key} — {name}")
            color = "#50fa7b" if mx and pct > 66 else ("#f1fa8c" if mx and pct > 33 else "#ff5555")
            panel["hp"].configure(text=f"HP: {cur}/{mx} ({pct:5.1f}%)", foreground=color)
            m = data["meter"]; panel["meter"].configure(text=f"Meter: {m if m is not None else '--'}")
            x = "--" if data["x"] is None else f"{data['x']:.3f}"
            y = "--" if data["y"] is None else f"{data['y']:.3f}"
            panel["pos"].configure(text=f"Pos: X:{x}  Y:{y}")
            lh = data["last_hit"]; panel["last"].configure(text=f"LastDmg: {lh if lh else '--'}")

    def _append_hit(self, h):
        ts = h["ts"]; vic = h["victim"]; atk = h["attacker"]
        line = (f"[{int(ts)}] HIT  victim={vic.label}({vic.name:<16}) "
                f"dmg={h['dmg']:4d}  hp:{h['hp_from']}->{h['hp_to']}  "
                f"attacker≈{atk.label}({atk.name})  dist2={h['dist2']:.3f}\n")
        self.log.configure(state="normal")
        self.log.insert("end", line)
        if int(self.log.index('end-1c').split('.')[0]) > 500:
            self.log.delete("1.0", "50.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def on_close(self):
        try: self.poller.stop()
        except Exception: pass
        self.destroy()
