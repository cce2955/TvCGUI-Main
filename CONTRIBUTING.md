# Contributing

By submitting a contribution, you represent that you have the right to submit
it under the repository's MIT License and that it does not contain proprietary
game code, generated recompilation/decompilation output, extracted assets,
private memory dumps, secrets, or code copied from an incompatible license.

Contributions to the attack recorder must preserve its read-only design. That
path must not write to Dolphin or emulated game memory, inject code, install
runtime hooks, or patch the game executable. Optional training modules that do
write memory must remain isolated, explicitly labeled, and disabled by default.

Reverse-engineering findings should be expressed as independently written
factual documentation, tests, tables, or original code. Do not submit long
assembly listings, decompiler output, or binary-derived generated source.

Before opening a pull request:

1. Run the test suite.
2. Run `python tools/release_audit.py .`.
3. Confirm that new dependencies and assets are listed in
   `THIRD_PARTY_NOTICES.txt` with their licenses.
4. Explain the evidence level for new addresses, offsets, and bit meanings.
