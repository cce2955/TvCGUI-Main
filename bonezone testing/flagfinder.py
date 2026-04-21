"""
TvC Hitbox Scanner
Known: u16 at 0x9246BBE6 = 0x0018 (idle) / 0x0019 (hitbox active)

Goal: scan nearby memory for fields that ONLY change during 0x0019 state.
Those fields contain the hitbox position, size, type, damage, etc.

Strategy:
  1. Snapshot memory around the flag when idle (0x0018)
  2. Snapshot when attacking (0x0019)  
  3. Diff — changed fields are hitbox data
"""

import struct, sys, time, msvcrt, json
import dolphin_memory_engine as dme

HITBOX_FLAG   = 0x9246BBE6
FLAG_NEUTRAL  = 0x0000  # idle, no action
FLAG_STARTUP  = 0x0018  # animation playing (startup / recovery)
FLAG_ACTIVE   = 0x0019  # offensive hitbox live
SCAN_RADIUS   = 0x200

FLAG_IDLE     = FLAG_NEUTRAL  # diff baseline

def read_flag():
    try:
        data = dme.read_bytes(HITBOX_FLAG, 2)
        return struct.unpack('>H', data)[0]
    except:
        return None

def read_region(center, radius):
    start = center - radius
    size  = radius * 2
    try:
        return dme.read_bytes(start, size)
    except:
        return None

def snapshot():
    data = read_region(HITBOX_FLAG, SCAN_RADIUS)
    flag = read_flag()
    return data, flag

def diff_snapshots(idle_data, active_data):
    """
    Compare two memory snapshots. Return list of offsets that changed,
    with their values in both states.
    """
    if not idle_data or not active_data:
        return []
    
    changes = []
    base = HITBOX_FLAG - SCAN_RADIUS
    
    # Check every 2-byte aligned offset
    for i in range(0, len(idle_data) - 4, 2):
        idle_u16  = struct.unpack_from('>H', idle_data,  i)[0]
        active_u16 = struct.unpack_from('>H', active_data, i)[0]
        
        if idle_u16 != active_u16:
            addr = base + i
            # Also read as float and u32
            if i + 4 <= len(idle_data):
                idle_f   = struct.unpack_from('>f', idle_data,  i)[0]
                active_f = struct.unpack_from('>f', active_data, i)[0]
                idle_u32  = struct.unpack_from('>I', idle_data,  i)[0]
                active_u32 = struct.unpack_from('>I', active_data, i)[0]
            else:
                idle_f = active_f = 0
                idle_u32 = active_u32 = 0
            
            changes.append({
                'addr':      addr,
                'offset':    addr - HITBOX_FLAG,
                'idle_u16':  idle_u16,
                'act_u16':   active_u16,
                'idle_u32':  idle_u32,
                'act_u32':   active_u32,
                'idle_f':    idle_f,
                'act_f':     active_f,
            })
    
    return changes

def is_valid_world_float(f):
    return f == f and -50.0 <= f <= 50.0 and abs(f) > 0.001

# ══════════════════════════════════════════════════════════════
# IDLE/ACTIVE DIFF MODE
# ══════════════════════════════════════════════════════════════

def diff_mode():
    print("\n  ─── IDLE vs ACTIVE DIFF ─────────────────────────")
    print(f"  Flag address: 0x{HITBOX_FLAG:08X}")
    print(f"  Scan radius: ±0x{SCAN_RADIUS:X} bytes\n")

    # Step 1: get idle snapshot — 0x0000 = neutral, no action
    print("  Step 1: Stand completely still (no attack, no motion).")
    print(f"  Waiting for flag = 0x{FLAG_NEUTRAL:04X} (neutral)...")
    print("  Press Q to abort.\n")

    t0 = time.time()
    while time.time() - t0 < 30:
        if msvcrt.kbhit():
            if msvcrt.getch().lower() == b'q':
                print("\n  Aborted."); return
        flag = read_flag()
        flag_str = f"0x{flag:04X}" if flag is not None else "0x????"
        sys.stdout.write(f"\r  flag = {flag_str}  waiting for 0x{FLAG_NEUTRAL:04X}...   ")
        sys.stdout.flush()
        if flag == FLAG_NEUTRAL:
            # Hold for 0.5s to make sure we're genuinely idle
            time.sleep(0.5)
            if read_flag() == FLAG_NEUTRAL:
                print(f"\n  Confirmed neutral. Taking snapshot...")
                break
        time.sleep(1/120)
    else:
        print("\n  Timeout."); return

    idle_data, _ = snapshot()
    print(f"  Idle snapshot taken. ({len(idle_data)} bytes)\n")

    # Step 2: get active snapshot
    print("  Step 2: Do an attack. Script will auto-capture during active frames.")
    print(f"  Watching for flag to change from 0x{FLAG_IDLE:04X} to 0x{FLAG_ACTIVE:04X}...")
    print("  (Press Q to abort)\n")
    
    active_data = None
    t0 = time.time()
    
    while time.time() - t0 < 30:  # 30 second timeout
        if msvcrt.kbhit():
            if msvcrt.getch().lower() == b'q':
                print("\n  Aborted."); return
        
        flag = read_flag()
        flag_str = f"0x{flag:04X}" if flag is not None else "0x????"
        sys.stdout.write(f"\r  flag = {flag_str}  waiting for 0x{FLAG_ACTIVE:04X}...   ")
        sys.stdout.flush()
        
        if flag == FLAG_ACTIVE:
            active_data, _ = snapshot()
            print(f"\n  CAPTURED! flag=0x{FLAG_ACTIVE:04X}\n")
            break
        
        time.sleep(1/120)
    
    if not active_data:
        print("\n  Timeout. No active frame detected."); return

    # Step 3: diff
    changes = diff_snapshots(idle_data, active_data)
    print(f"  Found {len(changes)} changed values within ±0x{SCAN_RADIUS:X} of flag\n")
    
    if not changes:
        print("  No changes found. Try larger radius or different attack."); return

    # Categorize changes
    print(f"  {'Address':<12} {'Off from flag':>14}  "
          f"{'Idle':>10}  {'Active':>10}  type  notes")
    print(f"  {'-'*80}")
    
    float_candidates = []
    
    for c in changes:
        addr = c['addr']
        off  = c['offset']
        
        # Determine most likely type
        idle_f, act_f = c['idle_f'], c['act_f']
        
        notes = []
        type_str = "u16"
        
        # Check if it's a plausible world-space float
        if is_valid_world_float(act_f) and not is_valid_world_float(idle_f):
            notes.append("FLOAT_APPEARS←")
            type_str = "f32"
            float_candidates.append(c)
        elif is_valid_world_float(idle_f) and is_valid_world_float(act_f):
            if abs(act_f - idle_f) > 0.01:
                notes.append("FLOAT_CHANGES")
                type_str = "f32"
                float_candidates.append(c)
        
        # Is this the flag itself?
        if addr == HITBOX_FLAG:
            notes.append("← FLAG ITSELF")
        
        # Small integer changes
        if c['act_u16'] < 0x100 and c['idle_u16'] < 0x100:
            notes.append("small_int")
        
        idle_str  = f"0x{c['idle_u16']:04X}" if type_str == "u16" else f"{idle_f:.4f}"
        act_str   = f"0x{c['act_u16']:04X}" if type_str == "u16" else f"{act_f:.4f}"
        
        off_str = f"{off:+d} (0x{abs(off):X})" if off != 0 else "FLAG"
        print(f"  0x{addr:08X}  {off_str:>14}  {idle_str:>10}  {act_str:>10}  {type_str}  {' '.join(notes)}")
    
    # Highlight float candidates
    if float_candidates:
        print(f"\n  ══ FLOAT CANDIDATES (likely position/size) ══")
        for c in float_candidates:
            print(f"    0x{c['addr']:08X}  off={c['offset']:+d}  "
                  f"idle={c['idle_f']:.5f}  active={c['act_f']:.5f}  "
                  f"delta={c['act_f']-c['idle_f']:.5f}")
    
    # Save results
    results = {
        'flag_addr': hex(HITBOX_FLAG),
        'scan_radius': hex(SCAN_RADIUS),
        'total_changes': len(changes),
        'changes': [
            {
                'addr':    hex(c['addr']),
                'offset':  c['offset'],
                'idle':    hex(c['idle_u16']),
                'active':  hex(c['act_u16']),
                'idle_f':  c['idle_f'],
                'act_f':   c['act_f'],
            }
            for c in changes
        ]
    }
    with open('hitbox_diff.json', 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n  Saved → hitbox_diff.json")

# ══════════════════════════════════════════════════════════════
# LIVE MONITOR — watch nearby region during gameplay
# ══════════════════════════════════════════════════════════════

def live_monitor():
    print(f"\n  Monitoring 0x{HITBOX_FLAG-0x80:08X}–0x{HITBOX_FLAG+0x80:08X}")
    print(f"  Shows live values. Flag at +0x00 (0x{HITBOX_FLAG:08X})")
    print(f"  Q=quit\n")
    
    prev_flag = None
    
    while True:
        if msvcrt.kbhit():
            if msvcrt.getch().lower() == b'q': break
        
        flag = read_flag()
        if flag == FLAG_ACTIVE:
            state = "ACTIVE  !!!"
        elif flag == FLAG_STARTUP:
            state = "startup/recovery"
        elif flag == FLAG_NEUTRAL:
            state = "neutral"
        else:
            state = f"unknown 0x{flag:04X}"
        
        # Read ±0x40 as floats
        try:
            data = dme.read_bytes(HITBOX_FLAG - 0x40, 0x80)
        except:
            time.sleep(0.1); continue
        
        sys.stdout.write('\033[2J\033[H')
        print(f"  flag=0x{flag:04X}  [{state}]  addr=0x{HITBOX_FLAG:08X}\n")
        print(f"  {'Addr':<12} {'Off':>6}  {'u16':>6}  {'f32':>12}  {'u32':>10}")
        print(f"  {'-'*55}")
        
        for i in range(0, 0x80, 2):
            addr = HITBOX_FLAG - 0x40 + i
            off  = i - 0x40
            u16  = struct.unpack_from('>H', data, i)[0]
            
            # Also show as float if 4-byte aligned
            f_str = ""
            if i % 4 == 0 and i + 4 <= len(data):
                f = struct.unpack_from('>f', data, i)[0]
                if f == f and -100 < f < 100:
                    f_str = f"{f:12.5f}"
            
            u32_str = ""
            if i % 4 == 0 and i + 4 <= len(data):
                u32 = struct.unpack_from('>I', data, i)[0]
                u32_str = f"0x{u32:08X}"
            
            marker = " ◄ FLAG" if addr == HITBOX_FLAG else ""
            marker = " ◄ FLAG  !!!" if addr == HITBOX_FLAG and flag == FLAG_ACTIVE else marker
            
            print(f"  0x{addr:08X}  {off:+6d}  {u16:06X}  {f_str:>12}  {u32_str:>10}{marker}")
        
        sys.stdout.flush()
        time.sleep(1/30)
    
    print()

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║         TvC Hitbox Scanner                                   ║
╚══════════════════════════════════════════════════════════════╝
  Flag: 0x{HITBOX_FLAG:08X}
  Idle: 0x0018   Active: 0x0019
  Goal: find position/size/type data near the flag
""")
    while True:
        flag = read_flag()
        print(f"  Current flag: 0x{flag:04X}  ({'ACTIVE' if flag==FLAG_ACTIVE else 'idle'})")
        print()
        print("  ─── MENU ──────────────────────────────────")
        print("    [1] Idle vs Active diff  (best for finding hitbox data)")
        print("    [2] Live monitor  (watch raw values around flag in real time)")
        print("    [Q] Quit")
        ch = input("\n    Choice: ").strip().lower()
        if ch == '1': diff_mode()
        elif ch == '2': live_monitor()
        elif ch == 'q': print("\n  Bye."); break

if __name__ == "__main__":
    try:
        dme.hook()
        print("[OK] Hooked to Dolphin.")
    except Exception as e:
        sys.exit(f"[ERROR] {e}")
    main()