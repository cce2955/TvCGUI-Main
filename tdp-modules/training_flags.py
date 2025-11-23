# training_flags.py
#
# Training-mode / dummy control helpers split out of main.py
# so other tools (and the HUD) can reuse them.

from dolphin_io import rd8

# Training-mode / dummy control bytes (MEM1).
# These constants are useful for any external GUI or scripts.
TR_PAUSE_ADDR          = 0x803F562B
TR_CPU_DIFFICULTY_ADDR = 0x803F5640
TR_CPU_ACTION_ADDR     = 0x803F5643
TR_GUARD_MODE_ADDR     = 0x803F564B
TR_PUSHBLOCK_ADDR      = 0x803F564F
TR_BAROQUE_PCT_ADDR    = 0x803F565B
TR_ATTACK_DATA_ADDR    = 0x803F565F
TR_THROW_TECH_ADDR     = 0x803F5663
TR_DUMMY_METER_ADDR    = 0x803F566F
TR_DAMAGE_OUTPUT_ADDR  = 0x803F5677
TR_INPUT_DISPLAY_ADDR  = 0x803F567F
TR_PLAYER_LIFE_ADDR    = 0x803F5683
TR_PLAYER_METER_ADDR   = 0x803F5687
TR_BAROQUE_MODE_ADDR   = 0x803F568B

# Same addresses as a mapping used by the debug overlay
TRAINING_FLAGS = {
    # Global pause (training pause)
    "TrPause":       TR_PAUSE_ADDR,          # 00 normal, 01 paused

    # Dummy meter (CPU side)
    "DummyMeter":    TR_DUMMY_METER_ADDR,    # 00 normal, 01 recovery, 02 infinite

    # CPU action / behavior
    "CpuAction":     TR_CPU_ACTION_ADDR,     # 00 stand, 01 crouch, 02 jump, 03 super jump, 04 CPU, 05 player

    # CPU guard settings
    "CpuGuard":      TR_GUARD_MODE_ADDR,     # 00 none, 01 auto guard, 02 guard all

    # CPU pushblock
    "CpuPushblock":  TR_PUSHBLOCK_ADDR,      # 00 off, 01 pushblock enabled

    # CPU throw tech
    "CpuThrowTech":  TR_THROW_TECH_ADDR,     # 00 off, 01 always tech

    # Player 1 meter behavior
    "P1Meter":       TR_PLAYER_METER_ADDR,   # 00 normal, 01 recovery, 02 infinite

    # Player 1 life behavior
    "P1Life":        TR_PLAYER_LIFE_ADDR,    # 00 normal, 01 infinite

    # Free baroque mode
    "FreeBaroque":   TR_BAROQUE_MODE_ADDR,   # 00 normal, 01 free baroque

    # Baroque percent (0x00..0x0A => 0..100% in 10% steps)
    "BaroquePct":    TR_BAROQUE_PCT_ADDR,

    # Attack data overlay
    "AttackData":    TR_ATTACK_DATA_ADDR,    # 00 off, 01 on

    # Input display overlay
    "InputDisplay":  TR_INPUT_DISPLAY_ADDR,  # 00 off, 01 on

    # CPU difficulty (8 levels, 0x00,0x20,...,0xE0)
    "CpuDifficulty": TR_CPU_DIFFICULTY_ADDR, # default 0x20

    # Damage scaling
    "DamageOutput":  TR_DAMAGE_OUTPUT_ADDR,  # 00..03, default 01
}


def read_training_flags():
    """
    Read one byte from each training / dummy / display flag address.
    Returns list of (label, addr, value).
    """
    out = []
    for label, addr in TRAINING_FLAGS.items():
        try:
            val = rd8(addr)
        except Exception:
            val = None
        out.append((label, addr, val))
    return out
