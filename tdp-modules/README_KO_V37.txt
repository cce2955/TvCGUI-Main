KO Ctrl V37 — stock-latch trigger only

This is the full current advantage.zip build, not a partial/minimal source drop.
Every file from advantage.zip is preserved unchanged except main.py.

What changed in main.py:
- KO Ctrl no longer relies on one frame where both teams are still loaded and
  the final loser is visibly dead.
- It remembers per-team KO stocks for the entire match:
    normal team: 2 KO stocks
    giant team: 1 KO stock
- After the final stock is observed dead, FULL stays applied through pointer
  churn/result state. It returns to SAFE only after a new all-live roster has
  stayed visible for 0.75 seconds.

What did NOT change:
- hitboxesscaling.py / hurtbox drawing
- overlay code
- projectile logic
- the existing KO DOL packet instructions
