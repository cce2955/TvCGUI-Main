FAST LAUNCH V1

Frame Data now follows a strict no-click-scan rule:
- Clicking Frame Data never synchronously reloads/rebases profile JSON.
- It uses the most recent background worker snapshot.
- If the player/round has just changed and no snapshot exists yet, the worker
  warms it in the background instead of freezing the HUD.

The workbench also creates its native Toplevel immediately, shows a brief
"Opening saved profile" shell, then builds the controls/tree on the next Tk
idle tick. This removes the remaining dead-air before any window appears.
