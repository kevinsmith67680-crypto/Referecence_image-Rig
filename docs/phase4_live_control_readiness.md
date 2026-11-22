# Phase 4: live control readiness (Moho 14.5 SHOWTIME / Live Mode)

**Status: NOT STARTED, blocked twice.**

1. HARD GATE 4.0 has not passed. The request forbids live-control work (steps 16–18) before it does.
2. The Live Models discovery (`docs/live-models.md`, steps D0–D6) has not run. `research/moho-14.5/` is empty, so no
   Live Mode input identifier, `.mohoproj` key or Lua API for live inputs is known. CLAUDE.md forbids inventing any of
   them.

## What exists (all `proposed_unverified`)

| artefact | content | limit |
|---|---|---|
| `analysis/live_controls.json` (Phase 1/2) | candidate facial and body controls per character | no Moho input names |
| `analysis/live_artwork_manifest.json` | which artwork each live control would need | facial drawings are missing |
| `analysis/live_performance_controls.json` (Phase 3.6) | performance channels that could be live-driven | no Moho input names |
| `rig_controls` v1 (`motion/rigctl.py`) | rig controls with live inputs left **unassigned** | mappings `proposed_unverified` |
| LM-0 probe (`AutoRig_Probe.lua`) | read-only discovery of the Lua API surface and Smart Bone actions | never run in Moho |

The Phase 4.0 verify script adds a little discovery: the file- and render-related ScriptInterface names. It records
nothing about live inputs.

## What is needed to start

1. Pass HARD GATE 4.0 (`tools/macos/phase4_native_test.sh`).
2. Run the LM-0 probe on the reopened Phase 4.0 document, and follow the discovery protocol in `docs/live-models.md`:
   assign one camera input and one gamepad input by hand, save, and keep the files and screenshots in
   `research/moho-14.5/`.
3. Only then design the live control manifest (step 16), using identifiers taken from that evidence.

No webcam tracking runs in AutoRig. Reference-motion pose models run locally at import only (Phase 3.7).
