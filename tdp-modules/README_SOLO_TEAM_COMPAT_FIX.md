Solo Team compatibility fix
===========================

This build keeps:
- Extra Characters button: 3 inserted Yami entries only
  - after Gold Lightan -> Yami 3
  - after Zero        -> Yami 2
  - after Frank West  -> Yami 1
- X / Alt+F4 close handling from the previous build

Fix:
- The inserted-Yami roster refresher was fighting the old Solo Team profile-row helper.
- When Solo Team was ON, the game could enter its intermediate solo/profile state, but Extra Characters saw that as "patch missing" and immediately rewrote the count/table back to the inserted-Yami state.
- Now, once Solo Team has installed its profile rows, Extra Characters pauses its auto-repair loop until Solo Team is turned back OFF.
- Solo Team OFF restores only the Solo profile rows; it does not remove the 3 inserted Yami roster entries.
