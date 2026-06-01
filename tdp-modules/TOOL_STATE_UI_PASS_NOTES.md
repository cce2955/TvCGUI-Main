# Tool State UI pass

Changes made after the first Overseer implementation worked but felt too heavy.

- Renamed the visible button/window from Overseer to Tool State.
- Kept the internal function names stable so main.py wiring stays low risk.
- Split the window buttons into Status and Recovery rows.
- Added a hover help box at the bottom.
- Refresh and Dump State are clearly read/report actions.
- Safe Restore and Hard Reset are grouped separately as recovery actions.
