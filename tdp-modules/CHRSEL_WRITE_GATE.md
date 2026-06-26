# Character-Select Write Gate

The roster runtime is dormant when Extra Characters and Solo Team are both off and no explicit roster action is queued.

Active memory writes now occur only for:

- Extra Characters while armed
- Solo Team while armed
- A queued explicit roster action, such as restore or snapshot

The automatic character-select source rescue path is no longer invoked during the off state.
