# Phase 4.1 cloud checks: mock and simulation, not Moho evidence

Made by `.venv/bin/python tools/phase4_1_cloud_check.py`. For each character package it:
1. builds the full-body package;
2. runs the build, verify-after-reopen and IK/constraints scripts against the **strict mock** Moho API (the "reopen"
   is the same mock document);
3. stands in the **Python simulation** for the renders;
4. evaluates the Phase 4.1 gates.

Every gate comes out MOCK_ONLY or NOT_EXECUTED by design.

| file | content |
|---|---|
| `<character>/gate_4_1_mock.json` | the gates on mock evidence |
| `<character>/MOCK_phase4_1_*_report.json` | reports written by the mock (`moho_app_version` = `14.4-mock`) |
| `<character>/SIMULATION_pose_sheet.jpg` | the twelve test poses rendered by AutoRig's rigid simulation, each labelled SIMULATION |
| `<character>/native_skeleton.json`, `binding_plan.json`, `validation_plan.json`, `rig_spec.json`, `native_fullbody_manifest.json` | the package as built (the Mac path is baked in on the Mac) |
| `summary.json` | one line per character |

Real Moho evidence goes to `docs/phase4/evidence/native_runs_4_1/` only.
