# Invulnerability signature repair

- Captures the root-prefix form used by Ryu Shoryu L/M/H:
  `04 01 60 ... +0x1218 | 3F 00 00 00 | 00 00 NN 00`, where the move-table root points at the `3F` marker and the command header begins eight bytes earlier.
- Re-discovers exact `+0x1218` probes whenever a cached frame-data profile is opened. Older caches could refresh only retired probe addresses and therefore left Shoryu / Jun 6B blank.
- Bumps the frame-data profile schema to force one clean re-profile.
- Keeps confirmed command normals, including Jun 6B, in the root normal chain. Nearby unnamed multi-hit sections are now nested below their owning normal instead of creating a separate `6B linked sections` header.
