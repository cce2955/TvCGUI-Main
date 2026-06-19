V41 — FINAL-RESULT RESOLVER UNLOCK

Only main.py was changed.  Every original file in advantage.zip is preserved.

FULL KO Ctrl adds two DOL patches only after a full opposing-team KO:

800447E0: 64600400 -> 54600188
  Replace the result-lock OR with an operation that clears 0x04000000 in
  fighter+0x60.

800447E8: 4182000C -> 4800000C
  Continue into the ordinary action resolver instead of returning -2 when the
  fighter's result bit (+0x64 bit 0x40) is set.

The result manager is not written, rewound, or held. KO screen/camera/timing
remain owned by the game. SAFE and OFF restore both original instructions.
