# config.py
# Centralized runtime configuration and thresholds for TvC HUD + HIT/COMBO logger.
# These values control timing, thresholds, file naming, and signature scan parameters.

# ------------------- UI / Output -------------------
SHOW_HUD = True             # draw the static HUD in console
HUD_REFRESH_HZ = 20         # HUD redraw rate (Hz)

# ------------------- Polling -----------------------
POLL_HZ = 60                # memory poll rate (Hz)
INTERVAL = 1.0 / POLL_HZ

# ------------------- HIT / COMBO -------------------
COMBO_TIMEOUT   = 0.60      # seconds: combo ends if no new hit in this time
MIN_HIT_DAMAGE  = 10        # minimum damage delta to count as a valid hit
MAX_DIST2       = 100.0     # max squared distance to trust attacker guess
METER_DELTA_MIN = 5         # minimum meter delta to guess team

# ------------------- HUD thresholds ----------------
HP_MIN_MAX = 10_000
HP_MAX_MAX = 60_000
HP_GREEN   = 0.66
HP_YELLOW  = 0.33

# ------------------- Y auto-sampling ----------------
SAMPLE_SECS = 1.5           # seconds to sample for Y-offset detection
SAMPLE_DT   = 1.0 / 120

# ------------------- Filenames ---------------------
# Each session gets its own ./logs/YYYYMMDD_HHMMSS/ directory.
HIT_CSV              = "collisions.csv"
COMBO_CSV            = "combos.csv"
CHAR_AGNOSTIC_CSV    = "move_id_map_charagnostic.csv"
HIT_SIG_EVENT_CSV    = "hit_sig_events.csv"
HIT_SIG_SUMMARY_CSV  = "hit_signature_summary.csv"

# ------------------- Attack ID offsets --------------
ATT_ID_OFF_PRIMARY = 0x1E8
ATT_ID_OFF_SECOND  = 0x1EC

# ------------------- Signature miner ----------------
HIT_SIG_PRE_FRAMES   = 1      # frames before hit used as "pre"
HIT_SIG_POST_FRAMES  = 2      # frames after hit used as "post"
NONHIT_SAMPLE_PERIOD = 0.25   # seconds between background non-hit samples
HIT_SIG_TOPN         = 12     # show top-N offsets on HUD
HIT_SIG_SCAN_LO      = 0x000
HIT_SIG_SCAN_HI      = 0x600
HIT_SIG_STRIDES      = (1, 2, 4)
