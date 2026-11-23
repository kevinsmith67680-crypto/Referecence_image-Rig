# Phase 4.0: native proof rig

**Native status: PARTIAL (2026-11-23).** In Moho 14.5.1 the build script ran and the document was saved. Reopening, the
verify script and the renders were not executed. §6 has the native results; `phase4_completion_report.md` has the full
assessment. Every number marked *mock* below comes from the strict mock Moho API, and every image from the Python
simulation. None of them is Moho behaviour.

## 1. Commands

```bash
.venv/bin/python -m autorig native-poc <package> --out <dir> [--side l|r] [--canvas 1920x1080|source] [--asset-root <abs path on the Mac>]
.venv/bin/python -m autorig native-verify <dir> --evidence <evidence dir>
tools/macos/phase4_native_test.sh --custom-content "<Moho Custom Content Folder>" [--package <package>] [--side l|r]
.venv/bin/python tools/phase4_cloud_check.py      # mock + simulation check for both characters -> docs/phase4/cloud_checks/
```

Code: `src/autorig/native/poc.py`, `src/autorig/native/gate.py`, `src/autorig/data/templates/phase4_native_verify.lua`,
`lua_codegen.render_named_build_lua`. The build script is the Phase 0 template unchanged except for its names and labels
(the test checks that the line count matches), so it can sit next to the Phase 0 script in the menu.

## 2. What the package contains

| item | rule |
|---|---|
| `art/body_back.png`, `art/arm.png`, `art/body_front.png` | the package's **original** layers (category A) flattened in z order: the layers behind the chosen arm, the arm (upper arm, forearm, hand), and the layers in front of it. Pasted onto the Moho canvas by an **integer translation**: no resampling, no generated pixels |
| bones | `body` (root): pelvis head → chest tail; `shoulder` (parent `body`): clavicle head → tail; `arm` (parent `shoulder`): upper-arm head (the shoulder pivot) → forearm tail (the wrist). All read from `natural_pose/rig.json` by bone kind and side, then translated |
| bindings | `arm_<side>_art` → `arm`; `body_back_art`, `body_front_art` → `body` (rigid layer binding, as in Phase 0) |
| keys (build) | frame 1 rest; frame 12 arm lowered 45° and shoulder dropped 5°; frame 24 rest. "Lowered" is derived from the arm's direction (its tip goes down on screen) |
| key (after reopening) | frame 18: arm raised 30°, added only by the verify script |
| `rig_spec.json` | a Phase 0 rig spec (schema 0.1.0), so the Phase 0 verifier checks the build report unchanged |
| `expected/` | SIMULATION renders of frames 1, 12 and 18, and a labelled contact sheet |
| `native_manifest.json` | provenance: layer ids, z-order and pixel hashes (checked against the package manifest), offset, clipped pixels, bone sources, Moho rest values, keys |

Generated (Phase 2) pixels are excluded because they are pending review, so a gap shows under the arm when it moves.
The proof tests binding and persistence, not artwork quality.

The canvas defaults to 1920×1080, the size the Phase 0 procedure already uses. The 1086×1448 source is taller than
1080, so the window is chosen to keep the head, the shoulder, the pelvis and the arm's whole sweep in every keyed pose.
The legs below 1080 are cut off. `--canvas source` keeps the whole character and asks for a 1086×1448 Moho document.

## 3. Both characters (cloud: package built, scripts run in the mock)

| | schoolboy (`schoolboy_tpose`) | tough student (`tough_student_test`) |
|---|---|---|
| offset (canvas = source + offset) | (417, 0) | (417, 0) |
| arm layers | upper_arm_l, forearm_l, hand_l | upper_arm_l, forearm_l, hand_l |
| layers behind / in front | 12 / 10 | 12 / 10 |
| alpha px: body_back / arm / body_front | 210,577 / 44,068 / 73,222 | 220,753 / 43,252 / 71,369 |
| clipped alpha px (legs below the canvas) | 84,557 (body_back only; arm 0) | 93,853 (body_back only; arm 0) |
| arm on canvas at frames 12 / 18 | 0.9997 / 0.9998 | 0.9997 / 0.9997 |
| split fidelity (regrouped vs full composite) | max diff 0 | max diff 0 |
| `body` px | (959.2, 753.8) → (957.5, 412.2) | (960.7, 774.1) → (958.1, 396.0) |
| `shoulder` px | (1002.2, 455.7) → (1102.6, 468.5) | (1001.2, 443.4) → (1095.6, 456.6) |
| `arm` px | (1102.6, 468.5) → (1342.4, 474.7) | (1095.6, 456.6) → (1345.3, 467.6) |
| `arm` Moho local angle / length | 0.1010 rad / 0.4442 | 0.0949 rad / 0.4629 |
| build script in mock | completed; Phase 0 verifier 0 FAIL (*mock*) | completed; 0 FAIL (*mock*) |
| verify script after simulated reopen | rig, bindings, keys preserved; key 18 read back (*mock*) | same (*mock*) |
| gate | BLOCKED (4 MOCK_ONLY, saved NOT_EXECUTED) | BLOCKED (same) |

Details: `docs/phase4/cloud_checks/<character>/` (`gate_4_0_mock.json`, `MOCK_*` reports,
`SIMULATION_expected_frames.png`). The simulated "reopen" is the same mock document. It proves the scripts' logic,
not Moho's persistence.

## 4. Gate thresholds (`native/gate.py`)

| constant | value | meaning |
|---|---|---|
| `FG_DIFF` | 40 | a render pixel is figure if a channel differs from the background by more than 40 |
| `MOVE_DIFF` | 48 | a pixel changed between two frames if a channel differs by more than 48 |
| `ALIGN_IOU_MIN` | 0.85 | rest render silhouette vs the expected silhouette (catches Moho placing the image differently) |
| `MOVE_IOU_MIN` | 0.60 | changed region of the render vs the expected changed region |
| `MOVE_RATIO_MIN` | 0.5 | changed render pixels / expected changed pixels (a frozen arm gives about 0) |

Renders of another size are resized when the aspect matches (within 1 %); otherwise the comparison fails. A
transparent export is compared on white; an opaque one on its median border colour. Near-white artwork against a white
background drops out of the figure mask (in the simulation on grey 236, the tough student's IoU is 0.94 against the
schoolboy's 0.98). The 0.85 threshold allows for that.

Negative tests (`tests/test_p4_native.py`): a frozen arm fails items 2 and 5. Art shifted by 120 px fails the
alignment. A render with another aspect fails. Mock reports never pass. Fixture inputs relabelled as native do pass, so
the acceptance path is reachable; those fixtures are test data, never evidence.

## 5. What the Mac run will tell us

On the first native run the gate either passes, or fails at a named check:

| check | what a failure would mean |
|---|---|
| Phase 0 verifier `world_matrix` | the parent-local `fPos` convention differs from the documented assumption |
| rest render alignment | Moho places or scales full-canvas images differently |
| frame-12 render | binding does not move the layer, or moves it about another pivot |
| verify read-back | save or reopen loses bones, bindings or keys |
| frame-18 render | a key added after reopening does not drive the artwork, or the file was not re-saved |

The verify report also records:
- whether bone fields follow `SetCurFrame` (information only);
- the file- and render-related ScriptInterface names the installed Moho exposes. Phase 4.1 decides from these whether to
  automate save, reopen and render.

## 6. Native results (schoolboy, left arm, Moho 14.5.1)

| check | native result |
|---|---|
| package on the Mac vs the cloud | same spec sha256 (`d053c46d…`), pixel-identical art. Only the PNG compression bytes differ |
| build script | completed; the Phase 0 verifier gives **40 PASS, 0 FAIL** on Moho's read-back (bones, `fMovedMatrix` world positions, keys, bindings, image paths, document) |
| saved document | Moho 14.5.1 project (`version` 1045): **15 structural checks PASS**. Each image layer is 3.555556 × 2.0 units, which fills the frame |
| artwork follows the arm bone | the user saw it at frame 12. Moho's thumbnail shows it. **No render**, so the gate item is open |
| reopen, verify script, frame 18 | not executed: the save landed in another run's folder and the harness stopped |

Evidence: `docs/phase4/evidence/native_runs/`. Cloud evaluation: `docs/phase4/native_assessment/20261123/`.

