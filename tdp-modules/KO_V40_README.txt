TvC KO Ctrl V40 — Ryu result-action source patch

What changed from V39:
- Removed the V39 per-frame battle-manager/fighter baseline writer.
  That writer is what caused repeated result-state flashing and eventual crash.
- FULL KO Ctrl now patches exactly one static Ryu result-action source:
    0x8006AA08  li r3,0x2A  ->  li r3,1
  The game maps result request 0x162 through this instruction to Ryu's
  victory action 0x2A. V40 maps it to native idle action 1 instead.
- Retains the existing FULL input packet and the existing 0x80048394
  action-1 result-lock-store suppression.
- Does not alter or omit any hitbox/hurtbox/projectile files.

Notes:
- This is intentionally Ryu-specific. Other characters may route their
  victory actions through neighboring map entries and need their own source
  identification after Ryu is stable.
- SAFE/OFF restores 0x8006AA08 to its original 0x3860002A via
  KO_DOL_ORIGINALS_U32.
