# Live Models (Moho 14.5): requirement, findings, design and discovery plan

## 1. Requirement

> AutoRig Studio must support generating **Moho 14.5-compatible Live Models**,
> with **Smart Bones and Actions mapped to the supported webcam and gamepad
> inputs**.

Interpretation (please confirm): "supported inputs" means the inputs that
**Moho 14.5's Live Mode** accepts: webcam face tracking and XInput gamepads.
AutoRig does not add an input layer of its own.

**Current stage: LM-0, research and discovery tooling.** Generating input
mappings is blocked on facts that only the user's Moho 14.5 can provide (§4).

## 2. What is known, with sources

| Fact | Source | Confidence |
|---|---|---|
| Moho 14.5 "SHOWTIME" was released in October 2026 as a free update for Moho 14 owners | [Lost Marble blog](https://moho.lostmarble.com/blogs/news/moho-14-5-showtime-design-rig-and-animate-characters-live-all-the-power-of-moho-now-live), [press release](https://www.openpr.com/news/4664782/moho-14-5-showtime-adds-real-time-character-and-vtuber) | High (vendor) |
| Live Mode drives a rigged character in real time from webcam face tracking, gamepad input and the physics engine; output goes to the Moho Virtual Webcam or OBS | press release; [Digital Production](https://digitalproduction.com/2026/10/21/moho-14-5-showtime-live-animation/) | High |
| Moho **Pro** 14.5 can *create* live models; **Debut** can only open and stream them | press release | High |
| Webcam: "simply create a Smart Bone and assign it to any of the camera inputs". Inputs include head turning, head tilting, blinking, mouth closing, lip pursing, "and many more" | Lost Marble blog (via search excerpt); [ZKO quick take](https://zko.world/blog/moho-14-5-live-mode-webcam-puppeteering-quick-take) | High for the mechanism, **unknown for the full list and the identifiers** |
| Gamepad: any XInput controller; buttons and thumbsticks; assigned in the action settings with **"Select Input"** | press release; [YouTube tutorial](https://www.youtube.com/watch?v=YQTLe3B2ZkE) (title and description only) | Medium |
| A microphone (volume) can drive a Smart Bone | ZKO quick take | Low (secondary) |
| Existing rigs can be made live without rebuilding; Live Playback can record keys and audio to the timeline | press release | High |
| 14.5.1 has follow-up changes for Live Models | [YouTube "FREE LIVE PLAYBACK MODE – Moho 14.5.1"](https://www.youtube.com/watch?v=ayTss-gPRls) (title only) | Low |
| Scripting additions in 14.5: `ImageLayer:BeginFacialTracking/EndFacialTracking` (video face tracking), `AudioLayer:AnalyzePhonemes`, `ScriptInterface:Play`, among others. **No live-input assignment API was found** | [mohoscripting.com 14.5 features](https://mohoscripting.com/moho_versions/features/scripting-conventions) (search excerpt) | Medium: the excerpt may be incomplete |
| Action API (older than 14.5): `MohoLayer:ActivateAction(name)`, `CountActions()`, `ActionName(i)`, `IsSmartBoneAction(name)`, `InsertAction(name, frame, byRef)`; `AnimChannel:ActivateAction` (v12) | mohoscripting.com class pages (excerpts) | High |
| `ActivateAction` appears to **create** a missing action: community code activates `"junk"` and then calls `DeleteAction("junk")`; `MohoDoc:SetCurrentDocAction(nil)` returns to the mainline (v10+) | Strider22 `msCopyAction.lua` (Playpen folder, read in full) | Medium |
| A `.moho` file is a zip containing a JSON `*.mohoproj` (plus thumbnail and resources); Moho 13+ opens a bare `.mohoproj` | [Lost Marble forum](https://www.lostmarble.com/forum/viewtopic.php?p=173914); [moho-template](https://github.com/definitelyokay/moho-template) | High |

## 3. Capability matrix

| Element of a Live Model | How AutoRig would produce it | Status |
|---|---|---|
| Bones, including Smart Bone controller bones | Lua `AddBone` (already allowlisted) | Available (Phase 0 code path) |
| Smart Bone actions and regular actions with keyed poses | Lua `ActivateAction` + channel keys + return to mainline | **Evidence partial (U4)**: needs a native experiment first |
| Face-part art (eyes open/closed, mouth shapes) | Extend the art generator; switch layers or keyed visibility | Needs U5 |
| **Camera input → Smart Bone** | No Lua API known. Options: (a) generated step-by-step assignment sheet for the UI; (b) inject into the saved `.mohoproj` | **Blocked (U1, U3)** |
| **Gamepad input → action / Smart Bone** | Same two options | **Blocked (U2, U3)** |
| Verification | Probe + read-back report + Live Mode test with a real webcam and gamepad | Probe ready; rest after LM-1 |

## 4. Unknowns that block full automation

| ID | Unknown | How it will be resolved |
|---|---|---|
| U1 | Exact camera input list: UI labels, stored identifiers, value ranges, and how they map onto a Smart Bone's angle | Screenshots of the input list + `.moho` diff (D3) |
| U2 | Gamepad input identifiers (buttons, stick axes, triggers?), whether sticks can drive Smart Bones, and hold vs. toggle semantics | Screenshots + `.moho` diff (D4) |
| U3 | Where Moho stores input assignments, and whether Lua can read or write them | `autorig moho-file diff` on D1/D3/D4 files; probe keyword hits (D2/D5) |
| U4 | Lua authoring of (Smart Bone) actions: creation, keying, return to mainline | Small native experiment script (LM-1) |
| U5 | Best way to swap bitmap face parts in actions (switch layers vs. keyed visibility) | Moho docs/experiment (LM-1) |
| U6 | Whether physics settings matter for the generated models | Out of scope until LM-3 |

Rule: **no input identifier, file key or API name is invented.** The future
input catalogue (`src/autorig/data/moho_live_inputs.json`) will be filled only
from discovered evidence, and the rig spec will reject inputs that are not in it.

## 5. Proposed design (draft, subject to discovery results)

**Rig spec v0.2: new optional `live` section** (illustrative; the input
identifiers are placeholders until U1/U2 are resolved):

```json
"live": {
  "moho_min_version": "14.5",
  "smart_bones": [
    {"name": "sb_head_turn", "parent": "torso", "base_px": [1100, 300], "tip_px": [1100, 250],
     "range_deg": [-45, 45],
     "poses": [{"at_deg": 45, "bones": {"head": {"rotation_offset_deg": 12}}},
               {"at_deg": -45, "bones": {"head": {"rotation_offset_deg": -12}}}]}
  ],
  "actions": [{"name": "wave", "frames": 24, "tracks": ["… same format as animation.tracks …"]}],
  "inputs": [
    {"device": "camera",  "input": "<id from catalogue>", "target": {"smart_bone": "sb_head_turn"}},
    {"device": "gamepad", "input": "<id from catalogue>", "target": {"action": "wave"}}
  ]
}
```

**Generation pipeline**

1. Lua builds the Phase 0 rig, then the Smart Bone controller bones and their
   actions (after the U4 experiment confirms the API).
2. Input mapping takes one of two routes:
   - **Semi-automatic (fallback, always possible):** a generated input assignment
     sheet giving the exact UI steps per Smart Bone and action.
   - **Automatic (target):** `autorig moho-file inject-live-inputs` rewrites the
     saved `.moho` with the mappings, once U3 shows where and how they are stored.
     The file is validated by reopening it in Moho.
3. Read-back: extend the report with the action list, Smart Bone flags and,
   if they are visible, the input assignments.

**Live test character:** split the head art into a head base, eyes (open and
closed) and mouth (closed and open), so blink and mouth inputs have something
to drive. Proposed mapping: camera head turn → `sb_head_turn`, head tilt →
`head` angle Smart Bone, blink → eyes, mouth → mouth. On the gamepad, a button
triggers the `wave` action, and a stick, if allowed, drives an arm Smart Bone.

## 6. Discovery protocol (on the Mac with Moho Pro 14.5)

Tools prepared in the cloud: the read-only **AutoRig Probe** menu script and
`autorig moho-file inspect|diff`. All outputs go into `research/moho-14.5/`,
which is committed so later cloud sessions can read it.

| Step | Action | Output |
|---|---|---|
| D0 | Update to Moho 14.5.x (free for 14 owners). Note Help → About | Version string |
| D1 | Phase 0 build on a new 1920×1080 document. This also performs the Phase 0 native verification. Save the document | `L0_baseline.moho`, `phase0_moho_report.json` |
| D2 | Run **Scripts → AutoRig → AutoRig Probe** (read-only) | `autorig_probe_report.json` → rename to `probe_L0.json` |
| D3 | Add a bone `sb_head_turn` (child of `torso`), make its Smart Bone action rotate `head`, assign **one camera input** (head turning). Screenshot the full camera input list. Save as a new file | `L1_camera.moho`, `camera_inputs.png` |
| D4 | Create an action `wave` (keys on `upper_arm`) and assign **one gamepad button** with "Select Input". If the UI allows, also assign a stick to a Smart Bone. Screenshot the gamepad input list. Save as a new file | `L2_gamepad.moho`, `gamepad_inputs.png` |
| D5 | Run the probe again | `probe_L2.json` |
| D6 | Commit and push `research/moho-14.5/` (or send the files) | — |

Analysis in the cloud afterwards:

```bash
python -m autorig moho-file diff research/moho-14.5/L0_baseline.moho research/moho-14.5/L1_camera.moho
```

```bash
python -m autorig moho-file diff research/moho-14.5/L1_camera.moho research/moho-14.5/L2_gamepad.moho
```

```bash
python -m autorig moho-file inspect research/moho-14.5/L2_gamepad.moho --grep "input|live|gamepad|camera"
```

## 7. Staged plan and gates

| Stage | Scope | Gate to start |
|---|---|---|
| **LM-0** (done) | Research, this design, probe script, `.moho` inspect/diff tool | — |
| LM-1 | Input catalogue from evidence; spec v0.2 `live` schema; Lua Smart Bone and action generation (allowlist and mock extended with evidence); U4/U5 experiment script | Phase 0 natively verified **and** D0–D6 data available |
| LM-2 | Input mapping: assignment sheet (semi-automatic) and/or `.moho` injection (automatic, depends on U3); live test character | LM-1 natively verified |
| LM-3 | Native Live Mode test with webcam and XInput gamepad; record with Live Playback and verify the keys | LM-2 |

The SAM 3 gate is unchanged: it also waits for the Phase 0 native verification.
The relative order of SAM 3 and Live Models after Phase 0 is the user's choice.
