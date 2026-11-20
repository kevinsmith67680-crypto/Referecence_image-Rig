# Phase 0: native Moho verification checklist

Every check below requires **Moho Pro 14 running on the Mac**. None of them has
been executed. Status: **NOT EXECUTED**.

Reference images for visual comparison (Python simulation, not Moho renders):
`examples/phase0_test_character/preview/contact_sheet.png` and `phase0_preview.gif`.
Reference numbers: `examples/phase0_test_character/expected/expected_rig.json`.

Fill in the **Result** column with PASS, FAIL or N/A, and add notes.

## A. Automated native checks

| ID | Check | How | Expected | Result |
|---|---|---|---|---|
| A1 | Script loads | Restart Moho, open the Scripts menu | **Scripts → AutoRig → AutoRig Phase 0: Build Test Rig** is listed | |
| A2 | Build runs | New 1920×1080 document, run the script | Info alert "test rig built"; no `ERROR` lines in the Lua console | |
| A3 | Read-back verifies | `python -m autorig verify-report …` | `VERDICT: PASS`, 0 FAIL | |
| A4 | Native pytest | `AUTORIG_MOHO_REPORT=… pytest -m moho_native -v` | 2 passed | |
| A5 | Lua version | Report field `lua_version` | `Lua 5.4` | |

## B. Structure in the Moho UI

| ID | Check | How | Expected | Result |
|---|---|---|---|---|
| B1 | Layer tree | Layers panel | Bone layer `AutoRig_Phase0` containing `forearm_art`, `upper_arm_art`, `head_art`, `torso_art` (top to bottom) | |
| B2 | Bones | Select `AutoRig_Phase0`, choose a bone tool | 4 bones named `torso`, `head`, `upper_arm`, `forearm` | |
| B3 | Hierarchy | Select each bone and check its parent (or highlight with the Select Bone tool) | head → torso, upper_arm → torso, forearm → upper_arm, torso = root | |
| B4 | Bindings | Select each image layer; check the bound bone (Bind Layer tool or layer settings) | Each `*_art` layer is bound to the bone with the same name | |
| B5 | Timeline | Timeline range | Start 1, end 36 | |

## C. Alignment (the most important visual check)

| ID | Check | How | Expected | Result |
|---|---|---|---|---|
| C1 | Art fills the frame correctly | Frame 0, camera view | Character matches contact-sheet cell "frame 0": centred horizontally, head near the top, arm to the right | |
| C2 | Pivots on joints | Frame 0, bone layer selected | Torso bone runs from the hips up to the neck; the head bone starts at the neck; the upper-arm bone runs from the shoulder to the elbow; the forearm bone from the elbow to the wrist | |
| C3 | Z-order | Frame 0 | Forearm in front of upper arm; upper arm and head in front of the torso | |

## D. Animation

| ID | Check | How | Expected | Result |
|---|---|---|---|---|
| D1 | Key poses | Go to frames 10, 19, 28, 36 | Poses match the contact-sheet cells; frame 36 equals frame 1 | |
| D2 | Joint integrity | Scrub 1–36 slowly | No gaps or detachment at the neck, shoulder or elbow | |
| D3 | Playback | Play with looping | Smooth wave with a slight torso sway; one-frame hold at the loop seam (frames 36 → 1 are identical) | |
| D4 | Interpolation | Inspect keys in the timeline | Keys at 1, 10, 19, 28, 36 on all four bones; note whether interpolation is smooth or linear | |

## E. Binding behaviour (manual poke, then undo)

| ID | Check | How | Expected | Result |
|---|---|---|---|---|
| E1 | Forearm only | Frame 5: rotate `forearm` with the Manipulate Bones tool, then Cmd+Z | Only the forearm art moves | |
| E2 | Chain | Frame 5: rotate `upper_arm`, then Cmd+Z | Upper arm and forearm move together around the shoulder | |
| E3 | Root | Frame 5: rotate `torso`, then Cmd+Z | Whole character rotates around the hips | |

## F. Persistence

| ID | Check | How | Expected | Result |
|---|---|---|---|---|
| F1 | Save and reopen | Save As `.moho`, close, reopen (SSD mounted) | Rig, bindings and animation intact; images load | |
| F2 | Optional render | Export frames 1–36 | Matches `phase0_preview.gif` motion (colours and antialiasing may differ) | |

## Sign-off

- Moho version: ____________ macOS version: ____________
- A–F all PASS → Phase 0 verdict can be raised to **PASS**, which unblocks Phase 1 (SAM 3).
- Any FAIL → keep **PARTIAL/FAIL**. Send the report JSON, console output and
  screenshots so the cause can be fixed in the cloud.
