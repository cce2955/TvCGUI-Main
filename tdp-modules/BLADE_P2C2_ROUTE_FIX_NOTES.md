Changed:
- assist_scanner_backend.py

Reason:
- Tekkaman Blade on P2-C2 can place the active chr_tbl before the nominal P2-C2 owner base.
- Observed session:
  - P2-C2 owner base: 0x9099D9C0
  - Blade dump scan start / live table region: 0x90986000
  - delta: 0x179C0 before owner base
- The route resolver only scanned back 0x12000, so it missed the live chr_tbl and produced:
  [assist quick] no assist route for P2-C2 base 0x9099D9C0

Fix:
- Increased CHR_TBL_PRE_START_BACK from 0x12000 to 0x24000.
- This remains below the broad inter-slot gap, so it should catch Blade's pre-base table without crossing into the previous character slot.

No assist write logic changed.
No Tool State/main dock/UI changes were intentionally removed.
