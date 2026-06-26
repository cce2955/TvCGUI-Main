# Character-Select Off-State Isolation

With Extra Characters and Solo Team disabled and no queued action, the character-select runtime is not scheduled and performs no character-select memory reads or writes.

The toggle handlers remain available. A toggle operation activates runtime service only long enough to process the request.

Validation target:
- Extra Characters: OFF
- Solo Team: OFF
- No pending restore action
- Character select remains visually stable
