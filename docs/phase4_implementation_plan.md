# Phase 4: implementation plan

Phase 4 turns the AutoRig character package into a native Moho rig. The request orders the work so that
nothing is built on unverified native behaviour. Phase 4.0 is the smallest native proof, and **HARD GATE 4.0** must
pass before any of steps 5–23 starts.

Current position: **Phase 4.0 is built and cloud-tested; its native run has not happened.** The gate is blocked (see
[`phase4_completion_report.md`](phase4_completion_report.md)). Steps 5–23 below are a plan only, and none of them has
started.

## Phase 4.0: minimal native proof (steps 1–4)

| step | deliverable | status |
|---|---|---|
| 1. audit the existing integration | [`phase4_native_integration_audit.md`](phase4_native_integration_audit.md) | done |
| 2. verify the installed Moho 14.5 environment and API | the harness records the bundle version, `AppVersion()`, Lua version and the file-related API names on the first run | **blocked**: no Mac attached |
| 3. local Mac execution bridge | `tools/macos/phase4_native_test.sh` (guided run, evidence collection, gate check) | built; cloud-tested on its failure paths only |
| 4. minimal native proof | `autorig native-poc` package + `AutoRig_P4_NativeBuild.lua` + `AutoRig_P4_NativeVerify.lua` + `autorig native-verify` | built; mock-tested; **native run NOT EXECUTED** |

The proof covers each item the request lists:

| requested | how |
|---|---|
| document | File > New (manual, no evidenced API) |
| real artwork layer | the character's original pixels, flattened in z order and pasted unscaled (integer translation) |
| bone layer, shoulder bone, arm bone | `body` (root), `shoulder` (clavicle), `arm` (shoulder pivot to wrist), from `natural_pose/rig.json` |
| attach | the arm layer is bound to `arm` with `SetLayerParentBone`; the body layers to `body` |
| manipulate | build keys: frame 12 lowers the arm 45° and drops the shoulder 5° |
| save, close, reopen | Save As, Close (manual); the harness reopens with `open -a` |
| move again | the verify script keys `arm` at frame 18 (raised 30°) on the reopened document and reads the key back |
| render a frame | frames 1, 12 and 18 from the saved document: Moho command line if it works, else File > Export Animation |

### HARD GATE 4.0

`autorig native-verify` (`src/autorig/native/gate.py`) checks five items. Each needs evidence from the real application.
Mock reports and simulated renders give MOCK_ONLY, never PASS.

| gate item | evidence | check |
|---|---|---|
| a real native document was created | build report written by Moho | origin native; the Phase 0 verifier has 0 FAIL |
| at least one bone genuinely moves artwork | Moho renders of frames 1 and 12 | rest silhouette IoU ≥ 0.85; changed-region IoU ≥ 0.60; changed ratio ≥ 0.5 |
| the document saves | the saved `.moho` | it exists, and the rig's names are in the parsed project (or the native reopen found the rig) |
| the document reopens | verify report written by Moho | the bones' rest values, parents, build keys and bindings are all preserved |
| the bone works after reopening | verify report + Moho render of frame 18 | the new key reads back, and the frame-18 render matches the raised arm |

## After the gate (steps 5–23, not started)

Each step is planned around the evidence that would prove it. None will be written before 4.0 passes, and each sub-phase
again stops at a native check.

| sub-phase | steps | content | native evidence needed |
|---|---|---|---|
| 4.1 export + coordinates | 5, 6 | full export package: all original layers unscaled, the full skeleton, provenance; pixel↔Moho conversion generalised from Phase 0 | rest render matches the source composite (silhouette and pixel diff) |
| 4.2 layers, bones, bindings | 7, 8 | layer builder (groups, draw order), bone builder (full hierarchy), bindings. Smooth/point binding only if evidenced; rigid binding otherwise | per-layer read-back; per-joint renders at fixed angles |
| 4.3 IK and switches | 9, 10 | Moho IK only if the API is evidenced, otherwise FK keys from AutoRig's one solver; switch layers for hands (approved drawings only) | switch read-back; renders per switch value |
| 4.4 correctives and shoulders | 11, 12 | corrective layers (approved assets only) and the shoulder benchmark (0/30/60/90/120°) | sweep renders vs AutoRig previews |
| 4.5 gestures, actions, animation | 13–15 | gesture actions, Moho Actions, animation transfer from the performance engine (keys only; no second solver) | action list read-back; frame renders vs AutoRig frames |
| 4.6 live foundation | 16–18 | live control manifest, SHOWTIME / Live Mode mapping, live demo | needs Live Models discovery (D0–D6) first; no input ids invented |
| 4.7 lifecycle, parity, benchmarks, regression | 19–23 | rebuild/update lifecycle, visual parity reports, timings on the M1, full regression | native renders and timings from the Mac |

Rules carried into every sub-phase:
- no Moho API member without evidence plus a mock implementation (CLAUDE.md rule 2);
- no pending artwork approved in code; pending drawings are never exported as production layers;
- no character-specific coordinates: everything comes from the package (the Phase 4.0 package is built the same way for
  the schoolboy and the tough student);
- no Moho screenshots or renders are produced or edited in the cloud; simulations are labelled SIMULATION.
