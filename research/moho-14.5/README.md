# research/moho-14.5: discovery data from the user's Moho 14.5

This folder holds evidence gathered **on the Mac** with Moho Pro 14.5 for the
Live Models track (see `docs/live-models.md` §6). Files here are committed on
purpose so that cloud sessions, which have no Moho, can analyse them.

| File | Produced by | Step |
|---|---|---|
| `L0_baseline.moho` | Phase 0 build, then File → Save As | D1 |
| `phase0_moho_report.json` (copy) | Phase 0 build | D1 |
| `probe_L0.json` | Scripts → AutoRig → AutoRig Probe (writes `autorig_probe_report.json` here; rename it) | D2 |
| `L1_camera.moho`, `camera_inputs.png` | One camera input assigned to a Smart Bone by hand | D3 |
| `L2_gamepad.moho`, `gamepad_inputs.png` | One gamepad input assigned to an action by hand | D4 |
| `probe_L2.json` | Probe run after D4 | D5 |
| `NOTES.md` | Moho version (Help → About) and anything surprising | all |

Status: **no Live Mode discovery data yet** (steps D0–D6 not run).

## First native Moho 14.5.1 project file (Phase 4.0, 2026-11-23)

The Phase 4.0 native run saved a real Moho document:
`docs/phase4/evidence/native_runs/20261123T105258Z_schoolboy_tpose_l/AutoRig_P4_Native.moho`. It was saved during
run `20261123T105654Z`; see `docs/phase4_completion_report.md` §2. It is the first evidence of the file format.
These facts are **observed in that file**. They are not documentation, and other documents may contain more:

| item | observed |
|---|---|
| container | zip with `Project.mohoproj` (UTF-8 JSON) and `preview.jpg` (512×288 viewport thumbnail, with grid and bone overlays) |
| header | `mime_type` `application/x-vnd.lm_mohodoc`, `version` 1045, `created_date` / `modified_date` strings, `doc_uuid`, base64 `thumbnail` |
| document | `project_data.width/height/start_frame/end_frame/fps`, `back_color` RGBA |
| layers | `layers[]` with `type` (`MeshLayer` for the default "Layer 1", `BoneLayer`, `ImageLayer`), `name`, `uuid`, `parent_bone`, `origin`, `transforms`; a bone layer holds its children in `layers[]` (order: first created first) |
| image layer | `image_path` (absolute), `image_fileref` {`relativeTo`: `Absolute`, `path`, `complex_path` relative to the .moho}, `width` / `height` in Moho units (a 1920×1080 PNG in a 1920×1080 document: 3.555556 × 2.0, i.e. the frame) |
| skeleton | `skeleton.binding_mode` (1), `skeleton.bones[]` with `name`, `parent` (index, -1 = root), `length`, `offset`, `constraints`, `min_constraint`/`max_constraint`, dynamics settings, and channels `anim_pos`, `anim_angle`, `anim_scale`, `anim_parent`, `flip_h`, `flip_v`, `target_bone`, `ik_lock`, … |
| channels | `{type, ref, mute, when: [frames], val: [values], interp: [{im, v1, v2, in, h, s, t}]}`; keys set from Lua with `SetValue` were stored with `im: 1` (the meaning of the code is not confirmed) |
| precision | floats stored with 6 decimals |

`autorig moho-file inspect` reads it. `src/autorig/native/gate.py` (`saved_rig_checks`) uses only the keys listed here.
