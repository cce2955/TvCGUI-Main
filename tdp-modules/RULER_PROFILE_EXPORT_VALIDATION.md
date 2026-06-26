# Range profile export validation

The profile writer was checked against the schema that the ruler loads:

```json
{
  "schema": 5,
  "attacks": { "<char_id>:<move_id>": { "anchor": "fighter_root", "...": "..." } },
  "bodies": { "<char_id>": { "...": "..." } }
}
```

Export behavior:

1. Create same-directory temporary JSON.
2. Serialize full `attacks` and `bodies` payload.
3. Flush/fsync where available.
4. Re-open and parse the temporary file before replacement.
5. Atomically replace the persistent JSON, with three short retries for transient Windows file locks.
6. Keep the prior file intact and retain dirty in-memory state if an export fails.

For a one-file EXE, the seed JSON inside `_MEIPASS` is copied once beside `TvCGUI.exe`; all later learned moves write to that external persistent copy.
