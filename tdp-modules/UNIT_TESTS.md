# Unit Tests

Run the test suite from the repo root with:

    run unit tests.bat

Or run it directly with Python:

    python tdp-modules/run_unit_tests.py

The test suite uses Python's built-in `unittest` module. It does not require Dolphin, pygame, or the overlay to be running.

Current coverage focuses on the parts that have broken or been risky recently:

- Mission JSON integrity: unique mission IDs, valid steps/goals, valid `pass` and `grace` fields.
- Mission-mode payload loading: selected mission behavior, setup debug flags, step colors, legacy progress support.
- Meter-refill missions: `ryu_008`, `saki_009`, and `alex_017` keep free meter enabled outside combo, disable it during combo, and restore saved flags when mission mode deactivates.
- Move-ID map lookup: decimal IDs such as `256` -> `5A` and `430` -> `assist standby`.

To run a single test file:

    python tdp-modules/run_unit_tests.py test_mission_manager_meter_refill.py
