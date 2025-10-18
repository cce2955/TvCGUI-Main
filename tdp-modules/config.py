# config.py
# Centralized runtime configuration and thresholds for TvC HUD + HIT/COMBO logger.

# ------------------- UI / Output -------------------
SHOW_HUD = True
HUD_REFRESH_HZ = 20

# ------------------- Polling -----------------------
POLL_HZ = 60
INTERVAL = 1.0 / POLL_HZ

# ------------------- HIT / COMBO -------------------
COMBO_TIMEOUT   = 0.60
MIN_HIT_DAMAGE  = 10
MAX_DIST2       = 100.0
METER_DELTA_MIN = 5

# ------------------- HUD thresholds ----------------
HP_MIN_MAX = 10_000
HP_MAX_MAX = 60_000
HP_GREEN   = 0.66
HP_YELLOW  = 0.33

# ------------------- Y auto-sampling ----------------
SAMPLE_SECS = 1.5
SAMPLE_DT   = 1.0 / 120

# ------------------- Filenames ---------------------
HIT_CSV              = "collisions.csv"
COMBO_CSV            = "combos.csv"
CHAR_AGNOSTIC_CSV    = "move_id_map_charagnostic.csv"
HIT_SIG_EVENT_CSV    = "hit_sig_events.csv"
HIT_SIG_SUMMARY_CSV  = "hit_signature_summary.csv"

# ------------------- Attack ID offsets --------------
ATT_ID_OFF_PRIMARY = 0x1E8
ATT_ID_OFF_SECOND  = 0x1EC

# ------------------- Signature miner ----------------
# Pre/post snapshot policy
HIT_SIG_PRE_FRAMES      = 1
HIT_SIG_POST_FRAMES     = 2          # (legacy single-post was 1)
HIT_SIG_POST_SAMPLES    = 4          # NEW: number of extra post samples
HIT_SIG_POST_SAMPLE_DT  = 0.02       # NEW: spacing between extra post samples (seconds)

# Background non-hit sampling
NONHIT_SAMPLE_PERIOD = 0.25

# HUD / reporting
HIT_SIG_TOPN    = 12

# Scan window (you can widen this; was 0x000..0x600 originally)
HIT_SIG_SCAN_LO = 0x000
HIT_SIG_SCAN_HI = 0x800        # NEW: widened
HIT_SIG_STRIDES = (1, 2, 4)
# Multi-post reversion sampling (for hit_signature.py)
HIT_SIG_POST_SAMPLES    = 4       # number of extra post snapshots per hit
HIT_SIG_POST_SAMPLE_DT  = 0.02    # delay between each post snapshot (seconds)
