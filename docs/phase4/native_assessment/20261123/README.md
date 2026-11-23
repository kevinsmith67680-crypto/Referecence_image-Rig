# Cloud assessment of the first native Moho runs (2026-11-23)

The evidence was produced on the user's Mac and lives in `docs/phase4/evidence/native_runs/`. This folder holds what the
cloud computed **from** that evidence: gate results from `autorig native-verify`. Nothing here was produced by Moho, and
no Moho file, report, render or screenshot was created or edited.

## Reproduce

```bash
.venv/bin/python -m autorig native-poc projects/schoolboy_tpose/character_package --out build/phase4_native/schoolboy_tpose_l \
  --asset-root /Volumes/SSD/AutoRig/Referecence_image-Rig/build/phase4_native/schoolboy_tpose_l
```

The cloud rebuild has the same spec (sha256 `d053c46d…`), and its art is pixel-identical to the Mac's. Only the PNG
compression bytes differ.

```bash
R=docs/phase4/evidence/native_runs
.venv/bin/python -m autorig native-verify build/phase4_native/schoolboy_tpose_l --evidence $R/20261123T105258Z_schoolboy_tpose_l --out gate_run1.json
.venv/bin/python -m autorig native-verify build/phase4_native/schoolboy_tpose_l --evidence $R/20261123T105654Z_schoolboy_tpose_l --out gate_run2.json
.venv/bin/python -m autorig native-verify build/phase4_native/schoolboy_tpose_l --evidence $R/20261123T105654Z_schoolboy_tpose_l \
  --saved $R/20261123T105258Z_schoolboy_tpose_l/AutoRig_P4_Native.moho --out gate_run2_with_saved_file.json
```

## Files

| file | evidence evaluated | result |
|---|---|---|
| `gate_run1_20261123T105258Z.json` | run 1 folder as committed | no build report. The `.moho` in that folder holds the rig, but nothing in run 1 ties it to a Moho session, so the save is UNLINKED |
| `gate_run2_20261123T105654Z.json` | run 2 folder as committed (the same result as the harness's own `gate_4_0.json`) | native document PASS; the other four NOT_EXECUTED |
| `gate_run2_with_saved_file.json` | run 2 + the `.moho` that run 2 saved into run 1's folder (why it belongs to run 2: `docs/phase4_completion_report.md` §2) | native document PASS, document saved PASS; three NOT_EXECUTED |

The gate checker changed after the runs: the save is now checked structurally against the saved project, and an
UNLINKED status was added. The harness's own `gate_4_0.json` files were made with the earlier checker. For the items
they evaluated, they agree with the files here.
