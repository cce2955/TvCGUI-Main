KO V39 — manager baseline latch

Only main.py changed.

The prior KO packet was live but Ryu still had the result action (0x2A), because
the battle/result manager had already transitioned from its normal fight state
to the post-KO result state. V39 captures the live match-manager state before
any team is fully KO'd, then holds that small state group during the post-KO
training window.

It also restores only the living active winner's own pre-KO action/control
snapshot when the result loop visibly overwrites it. It never uses the old
generic idle packet, and it does not touch hitboxes, hurtboxes, projectiles,
or any non-main.py files.

Visual confirmation: title bar shows [V39_MANAGER_BASELINE_LATCH].
