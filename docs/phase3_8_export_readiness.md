# Phase 3.8 export readiness: the new character's Moho-ready asset package

The new character's package was exported with the existing export (`autorig export-assets`, the same
`editor.export.export_package` the editor's Export tab uses). The result is in
`projects/tough_student_test/moho_asset_package/` and is summarised in
`docs/phase3_8/new_character_evidence/20_export_readiness.png`.

**The Phase 4 native exporter was not started.** No `.moho` file exists, and nothing here is a working native Moho
rig.

## 1. Result

| | |
|---|---|
| `validate_package` errors | **0** |
| quality gates | 9 PASS, 3 NEEDS_REVIEW, overall **NEEDS_REVIEW** |
| `production_ready` | **false** (by rule: approvals and artwork are missing) |
| files | 62: manifest, 27 layers + 16 hand-switch layers, 12 vector files, source, rig controls, hand switches, garment rig + weights, quality-gate report |

Gates:

| level | gate |
|---|---|
| PASS | original source image preserved (sha256 matches the Phase 1 record) |
| PASS | artwork layer dimensions: every layer 1086×1448, full canvas |
| PASS | coordinate alignment: layers full canvas; vectors and bones in source px |
| PASS | no missing referenced assets (60 files) |
| PASS | valid bone hierarchy: 19 bones, single root, no cycles, no zero-length bones |
| PASS | valid joint positions |
| PASS | valid layer bindings: every visible layer is bound to an existing bone |
| PASS | no unexpected neutral-pose gaps |
| PASS | original artwork preservation: originals hash-verified, neutral composite equals the source |
| NEEDS_REVIEW | unresolved deformation defects (Phase 2 pose defects still open) |
| NEEDS_REVIEW | facial artwork completeness: blinks, mouth shapes and head turn need new artwork |
| NEEDS_REVIEW | mandatory user approvals: 32 pending |

## 2. What step 21 asked for, and where it is

| item | where | tested here | needs native Moho |
|---|---|---|---|
| original artwork references | `source/`, `manifest.json` `source` + `provenance` | hash-verified | — |
| segmented layers | `layers/` (27 original + 16 hand-switch derived), `manifest.json` `layers` | full-canvas, bound to bones, neutral composite exact | import as image layers |
| bone hierarchy and pivots | `manifest.json` `bones` (19 bones; shoulder pivots garment-aligned) | hierarchy and joint validity gates | bone creation and binding (Phase 0 importer, NOT EXECUTED) |
| garment binding metadata | `garment_rig.json` (41 parts, classes), `garment_weights.npz` | meshes valid (`weights_valid: true`) | per-vertex weights are **not assumed to import** (`proposed_unverified`) |
| hand switch groups | `hand_switches.json`: groups `hand_r` and `hand_l`, 18 variants, 16 pending | validated metadata | switch layers in Moho: `not_implemented_in_native_moho` / `proposed_unverified` |
| approved corrective references | none approved: the 13 corrective assets are listed as pending, none exported as approved | approval rule enforced | — |
| pose action metadata | `rig_controls.json` (`autorig.rig_controls` v1, validated before export) | schema-validated | Moho actions and Smart Bones: `proposed_unverified`; live inputs unassigned |
| animation data | not part of the export folder: `analysis/performance_animation.json` and the editor project's performance document | written by `performance-evidence`; acting test rendered in AutoRig | Moho keyframes: not exported (no exporter) |
| missing-artwork report | `docs/phase3_8/new_character_evidence/missing_artwork_report.json`; export gates; `analysis/view_manifest.json` | aggregated from the evidence and the export | — |
| approval report | `docs/phase3_8/new_character_evidence/approval_report.json`; `manifest.json` `approvals` | every item pending or rejected; none approved in code | — |

## 3. Tested vs requires native validation

Tested in this cloud environment:
- the export runs on a new character;
- every file it references exists;
- schemas validate;
- the neutral composite equals the source;
- the editor exports the same package from the browser (e2e step `export`: NEEDS_REVIEW, `production_ready: false`);
- the project saves and reopens with identical state and an identical render (`19_project_reload.png`).

Requires native Moho and has **not** been run:
- importing the layers and bones;
- binding;
- smart bones and actions;
- switch layers;
- garment weights;
- the Phase 0 importer itself, whose native verification is still NOT EXECUTED.

## 4. Before Phase 4

Phase 4 should not start until all three are done:
1. Phase 0 native verification on the user's Mac.
2. Human review of the 16 hand drawings, the corrective assets and the generated extensions. These include two
   known-bad items: the eye whites under the irises, and the white cuff-end fill under the original hands.
3. The missing artwork: palm-up hands, an arms-down shoulder, the deep elbow, seated legs, side/3/4 views, and the
   facial sets.
