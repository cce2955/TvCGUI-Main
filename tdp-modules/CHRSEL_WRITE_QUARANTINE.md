# Character-select write quarantine

When character select is detected while Extra Characters and Solo Team are inactive, tool-side emulated-memory writes are blocked. Blocked requests are appended to `chrsel_write_trace.jsonl` in the working directory. The barrier is released outside character select or when Extra Characters or Solo Team is armed.
