#!/usr/bin/env python3
"""
Animation Call Tracer - Find ALL 01 0C 01 3C in Ryu's script region
"""

import tkinter as tk
from tkinter import scrolledtext
import threading

from dolphin_io import hook, rbytes

# Search region around your known address
SEARCH_START = 0x908C7000
SEARCH_END = 0x908C8000

class AnimationTracer:
    def __init__(self, root):
        self.root = root
        self.root.title("Find ALL Animation Calls")
        
        tk.Label(root, text="Searching for ALL instances of '01 0C 01 3C' in Ryu's script area",
                font=("Arial", 11, "bold")).pack(pady=10)
        
        self.status = tk.Label(root, text="Ready", font=("Arial", 10))
        self.status.pack(pady=5)
        
        tk.Button(root, text="SCAN NOW", command=self.scan,
                 font=("Arial", 12, "bold"), bg="lightgreen", 
                 height=2).pack(pady=10, padx=20, fill="x")
        
        self.text = scrolledtext.ScrolledText(root, width=100, height=30,
                                              font=("Courier", 9), bg="black", fg="lime")
        self.text.pack(padx=10, pady=10, fill="both", expand=True)
        
        threading.Thread(target=self._hook, daemon=True).start()
    
    def _hook(self):
        self.status.config(text="Hooking Dolphin...")
        hook()
        self.status.config(text="✓ Connected - Click SCAN NOW", fg="green")
    
    def scan(self):
        self.text.delete("1.0", "end")
        self.status.config(text="Scanning...")
        self.root.update()
        
        # Read the entire region
        size = SEARCH_END - SEARCH_START
        data = rbytes(SEARCH_START, size)
        
        if not data:
            self.text.insert("end", "Failed to read memory!\n")
            return
        
        self.text.insert("end", "="*80 + "\n")
        self.text.insert("end", f"SCANNING 0x{SEARCH_START:08X} - 0x{SEARCH_END:08X}\n")
        self.text.insert("end", "="*80 + "\n\n")
        
        # Find all instances of 01 0C 01 3C
        target = bytes([0x01, 0x0C, 0x01, 0x3C])
        matches = []
        
        offset = 0
        while True:
            idx = data.find(target, offset)
            if idx == -1:
                break
            matches.append(SEARCH_START + idx)
            offset = idx + 1
        
        if not matches:
            self.text.insert("end", "No instances of '01 0C 01 3C' found!\n")
            self.text.insert("end", "\nThis means the animation might be:\n")
            self.text.insert("end", "  1. Stored with different bytes\n")
            self.text.insert("end", "  2. In a different memory region\n")
            self.text.insert("end", "  3. Loaded dynamically from elsewhere\n")
        else:
            self.text.insert("end", f"Found {len(matches)} instance(s) of '01 0C 01 3C':\n\n")
            
            for i, addr in enumerate(matches, 1):
                offset = addr - SEARCH_START
                
                # Show context around each match
                ctx_start = max(0, offset - 32)
                ctx_end = min(len(data), offset + 32)
                context = data[ctx_start:ctx_end]
                
                self.text.insert("end", f"#{i}. ADDRESS: 0x{addr:08X}\n")
                self.text.insert("end", f"    Offset from search start: +0x{offset:04X}\n")
                
                # Show hex dump with marker
                self.text.insert("end", "    Context:\n")
                for j in range(0, len(context), 16):
                    line_addr = SEARCH_START + ctx_start + j
                    chunk = context[j:j+16]
                    hex_str = " ".join(f"{b:02X}" for b in chunk)
                    
                    marker = " <-- HERE" if line_addr == addr else ""
                    self.text.insert("end", f"      {line_addr:08X}  {hex_str}{marker}\n")
                
                self.text.insert("end", "\n")
        
        # Also search for 01 XX 01 3C pattern (any animation)
        self.text.insert("end", "\n" + "="*80 + "\n")
        self.text.insert("end", "ALL ANIMATION CALLS (01 XX 01 3C pattern):\n")
        self.text.insert("end", "="*80 + "\n\n")
        
        anim_calls = []
        for i in range(len(data) - 3):
            if data[i] == 0x01 and data[i+2] == 0x01 and data[i+3] == 0x3C:
                addr = SEARCH_START + i
                anim_id = (data[i] << 8) | data[i+1]
                anim_calls.append((addr, anim_id))
        
        if anim_calls:
            self.text.insert("end", f"Found {len(anim_calls)} animation call(s):\n\n")
            for addr, anim_id in anim_calls:
                self.text.insert("end", f"  0x{addr:08X}: Animation 0x{anim_id:04X}\n")
                
                # Highlight if it's 010C
                if anim_id == 0x010C:
                    self.text.insert("end", f"    ^^^ THIS IS 0x010C (Ryu assist)\n")
        
        self.text.insert("end", "\n" + "="*80 + "\n")
        self.text.insert("end", "RECOMMENDATION:\n")
        self.text.insert("end", "="*80 + "\n")
        self.text.insert("end", "If you found multiple 01 0C instances, try changing EACH ONE\n")
        self.text.insert("end", "and see which one actually affects the in-game animation.\n")
        self.text.insert("end", "\nThe one at 0x908C76C2 might be a backup/default value,\n")
        self.text.insert("end", "while the REAL active call is at a different address.\n")
        
        self.status.config(text=f"✓ Scan complete - Found {len(matches)} exact matches", fg="green")


if __name__ == "__main__":
    root = tk.Tk()
    app = AnimationTracer(root)
    root.mainloop()