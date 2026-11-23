# Phase 4.2 cloud output (SIMULATION / MOCK — not Moho evidence)

- `audit/<character>/`: the joint deformation audit (`autorig deform-audit`).
  - `joint_audit.json` holds the measurements, diagnosis and pivot counterfactual.
  - `SIMULATION_*.jpg` are close-ups and pose strips of the rigid native binding, per layer variant.
- `cloud_checks/<character>/`: `tools/phase4_2_cloud_check.py`.
  - The hybrid package's plans (binding, Smart Bone, artwork requests, manual tests).
  - The strict-mock build and verify reports (`MOCK_*`).
  - The gate result on mock + simulated evidence (`gate_4_2_mock.json`; never PASS).
  - The simulated pose sheets: built layers, planned Smart Bone fills, review build.

Native Phase 4.2 evidence goes only to `docs/phase4/evidence/native_runs_4_2/`, written on the Mac by
`tools/macos/phase4_native_test.sh --phase 4.2`. See `docs/phase4_2_moho_validation.md`.
