# Phase 4.0 cloud checks: mock and simulation, not Moho evidence

Made by `.venv/bin/python tools/phase4_cloud_check.py`. For each character package it:
1. builds the Phase 4.0 proof package;
2. runs the build script, then the verify-after-reopen script, against the **strict mock** Moho API. The "reopen" is
   simulated: it is the same mock document;
3. renders the expected frames with the **Python simulation**;
4. evaluates HARD GATE 4.0 on that evidence.

The gate is BLOCKED by design: every item is MOCK_ONLY, and the save is NOT_EXECUTED.

| file | content |
|---|---|
| `<character>/gate_4_0_mock.json` | the gate result on mock evidence |
| `<character>/MOCK_phase4_native_build_report.json`, `MOCK_phase4_native_verify_report.json` | reports written by the mock (`moho_app_version` = `14.4-mock`) |
| `<character>/SIMULATION_expected_frames.png` | Python renders of frames 1, 12 and 18 with the bones drawn; each cell is labelled SIMULATION |
| `<character>/rig_spec.json`, `native_manifest.json` | the proof package's spec and provenance (the Mac path is baked in on the Mac) |
| `summary.json` | one line per character |

Real Moho evidence goes to `docs/phase4/evidence/native_runs/` only.
