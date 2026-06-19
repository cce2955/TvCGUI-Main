KO Ctrl V38 - winner result-lock store fix

Scope: main.py only. All hitbox/hurtbox/projectile files are preserved unchanged.

Integrated evidence-based change:
- FULL KO Ctrl now NOPs 0x80048394 (original 0x901F0060: stw r0,+0x60(r31)).
- That store is the late winner-side result lock write that ORs 0x04002001 into
  the living winner's fighter+0x60 after win-pose suppression selects action 1/11.
- SAFE behavior and the existing working full input-feed path are unchanged.
- The NOP is FULL-only, and apply_ko_control_auto_mode restores 0x901F0060 when
  KO Ctrl returns to SAFE/OFF.

Build marker: KO_CTRL_BUILD = V38_RESULT_LOCK_STORE
