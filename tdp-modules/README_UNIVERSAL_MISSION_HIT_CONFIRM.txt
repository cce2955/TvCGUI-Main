TvCGUI universal Mission Mode hit-confirm fix

Changes:
1. Prediction never completes a mission step by itself.
2. Mission advancement still requires a real opponent hitstun state plus physical hit evidence.
3. A hit now retains every temporally eligible native action as a candidate source.
4. Ordered mission matching claims the candidate whose move label matches the expected step.
5. Delayed attacks, projectiles, releases, and moves started by a later trigger no longer lose credit to the newest action.
6. A hit that occurred before a later move began cannot be donated to that later move.
7. Assist actions remain excluded from the pinned point character's mission route.
8. Down, Down, Taunt reads the native point flag and reaffirms the point fighter after a real tag.

Validated with 33 focused non-graphics mission tests.
