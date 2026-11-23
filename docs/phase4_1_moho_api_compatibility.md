# Phase 4.1: Moho API compatibility

Target: Moho Pro 14.5.1 (Lua 5.4), the version on the user's Mac. Generated Lua may only use members of
`src/autorig/data/moho_api_allowlist.json`. The strict mock implements exactly that set, and every Phase 4.1 script
runs against it in the cloud suite.

## 1. Native status of the members used

| group | members | native status |
|---|---|---|
| build (the Phase 0 template) | `CreateNewLayer`, `LayerAsBone`, `LayerAsImage`, `PlaceLayerInGroup`, `SetLayerParentBone`, `SetSourceImage`, `Skeleton`, `AddBone`, `BoneID`, `Bone`, `CountBones`, `UpdateBoneMatrix`, bone fields, `AnimChannel:SetValue/GetValue/CountKeys/GetKeyWhen`, document getters and setters, `LM.Vector2`, `LM_Transform:Transform` | **ran in Moho 14.5.1** (Phase 4.0 run 2: completed, read-back matched). The full-body build calls the same members, more often |
| verify (4.1) | the read-back members above, plus `LayerByName`, `LayerAsGroup`, `CountLayers`, `Layer`, `LayerParentBone`, `fAnimPos:SetValue/GetValue` with `LM.Vector2` | the read-back members ran natively. `fAnimPos:SetValue` at frames other than 0, and the whole verify script, are **not yet run natively** |
| optional read in verify (4.1) | `fConstraints`, `fMinConstraint`, `fMaxConstraint`, `fTargetBone:GetValue` | read inside `pcall`; **not run natively** |
| IK / limits script (4.1, opt-in) | `fTargetBone:SetValue`, `fConstraints = true`, `fMinConstraint`, `fMaxConstraint` assignments, `fAnimPos` keys on targets, `SetEndFrame` | **not run natively**; every write is pcall-guarded and read back |

## 2. Members added to the allowlist in this phase

| member | evidence | mock |
|---|---|---|
| `M_Bone.fConstraints` (bool) | MS: M_Bone class reference (search snippet, "true if the bone has min/max angle constraints"); NF: key `constraints` on every bone of the native Moho 14.5.1 file | field, default `false` |
| `M_Bone.fMinConstraint` (real) | MS (search snippet, "minimum angle", < 9.5); NF: `min_constraint`, default −1.221731 | field, default −1.221731 |
| `M_Bone.fMaxConstraint` (real) | MS (search snippet, "maximum angle", < 9.5); NF: `max_constraint`, default 1.221731 | field, default 1.221731 |
| `M_Bone.fTargetBone` (AnimVal) | MS (search snippet, "ID of the current target bone", added in version 10); NF: `target_bone` channel, value −1 | channel, default −1 |

NF (new evidence source) is the Phase 4.0 native file: it shows the fields exist in Moho's document model. MS is
documentation read through search snippets, because the site is blocked here. Neither shows the semantics:
- are the constraint angles absolute, or relative to the rest pose?
- does a target constraint drive the chain at playback, or only while dragging?

Those are what the experimental script and its renders measure.

## 3. Discovery recorded by the verify script (no calls)

The verify script lists the names on the `ScriptInterface` metatable that look file- or render-related. It also
lists the names on a bone's metatable matching *target, constraint, ik, strength, fixed, lock, independent, angle,
scale, flip, smart, dynamics, physics*. Nothing found is called. The list feeds Phase 4.2's decisions, for example:
- `fFixedAngle` ("independent angle" for planted feet and hands; the native file has `fixed_angle`);
- save and render automation.

## 4. Still not allowlisted

| capability | why | Phase 4.1 handling |
|---|---|---|
| new / save / close / open document | evidence is secondary only (search snippets); `FileClose` has a reported bug | manual steps and `open -a` |
| render | no Lua member known | command-line renderer (secondary evidence, logged), else File > Export Animation |
| independent angle (`fFixedAngle`?), IK lock / ignore, bone strength | not needed yet; `fIgnoredByIK` appears on one reference page only | none; candidates for 4.2 |
| smooth / point binding of image layers, Smart Bones, Switch Layers | not evidenced | rigid layer binding; Smart Bones are 4.2, Switch Layers 4.3 |
| key interpolation | `SetKeyInterp` not evidenced for Moho 14 | not needed: every checked frame is a key frame |
