# Canonical action-root / Shoryu display repair

This build fixes three related Frame Data failures:

1. `chr_tbl` table indices are now treated as authoritative action IDs for unique move roots. Some real move roots begin on a typed scalar rather than an `ANIM_HDR`; Ryu Shoryu L/M/H are the confirmed case.
2. Interior script scans no longer create duplicate special rows when a canonical `chr_tbl` root already owns that action ID. This removes the false giant `Shoryu linked sections` group.
3. Small raw action IDs no longer borrow a `+0x100` name in the GUI. Raw `0x0036` remains `anim_0036` instead of being mislabeled as Ryu `0x0136 Shoryu L`.

The profile schema is v4. Existing cached profiles are intentionally rejected and rebuilt once, because they were saved before canonical table-root IDs were preserved.
