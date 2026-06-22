# Action-table catalog repair

The Frame Data window now builds its top-level rows from the character's `chr_tbl` action table, rather than from script-pattern hits.

- `0x0100–0x010F` are handled as exact action IDs. No low-byte matching is used, so system action `0x0001` can never replace `0x0101` / 5B.
- Top-level normal rows are the real player slots: 5A/5B/5C, crouching normals, 6C/3C, air normals, 6B, and j.2C. Legacy `0x0107` and second-hit continuations `0x010C/0x010D` stay hidden as implementation routes.
- `0x0110–0x012F` remains internal dispatch territory.
- Character-mapped rows from `0x0130+` become Specials, Supers, Throws, or mapped extras.
- The UI now has collapsible catalog sections: Normals, Throws, Specials, Supers, Assists, and Other actions.
- Field scanning is bounded by each table root's next unique root, so a child script cannot donate its data to another command row.
- The `+0x1218` phase signature is kept as raw evidence, but the visible `Invul` cell only shows phases of 3f or more. That leaves ordinary 2f housekeeping probes blank while preserving Ryu Shoryu 6f/10f/13f and Jun 6B 20f.

The frame-data profile cache schema is version 6. Existing cached character profiles rebuild once, intentionally.
