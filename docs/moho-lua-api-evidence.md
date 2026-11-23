# Moho Pro 14 Lua API: evidence for every member AutoRig uses

**Rule:** generated scripts may only use the members below. The machine-readable
list is `src/autorig/data/moho_api_allowlist.json`. The strict mock
`tests/lua/mock_moho.lua` implements exactly this set, and a test fails if the
two diverge.

## Research method and limits

- The unofficial reference **mohoscripting.com** and **lostmarble.com** (the
  manual and forum) were **not reachable** from the cloud sandbox (proxy refusal
  or DNS failure). Their content was read only as **web-search snippets**, which
  are short excerpts and not full pages.
- **Working third-party Lua code** was cloned from GitHub and read in full:
  - `SS`: [SimplSam/moho-make-bones](https://github.com/SimplSam/moho-make-bones), `ss_make_bones.lua` (targets AS11–MH13.5+; creates and parents bones)
  - `ST`: [Strider22/AnimeStudioScripts](https://github.com/Strider22/AnimeStudioScripts) (creates bone/group layers, moves layers into groups, binds layers)
  - `MCP`: [Kveto/MohoMCP](https://github.com/Kveto/MohoMCP) `moho-plugin/` (written for Moho 14 / Lua 5.4; read and transform tools)
- Nothing below has been executed in Moho by this project yet. "Evidence" means
  that the member is documented, or that it is used by scripts other people run
  in Moho.

## Environment facts

| Fact | Evidence |
|---|---|
| Moho 14 embeds **Lua 5.4** (5.4 vs 5.2 differ in the `math` library and bitwise ops) | mohoscripting.com front page (search snippet); MohoMCP README ("MOHO's Lua 5.4 API") |
| Menu-script structure: a `ScriptName` global, a table of that name, and `Name/Version/Description/Creator/UILabel/IsEnabled/Run(moho)` | mohoscripting.com `/script_structure` (snippet); MohoMCP `MohoMCP_Server.lua`; SimplSam |
| The file name should match `ScriptName` | Community install guide (snippet: "each file's name matches its internal script name") |
| `io`, `os` and `print` are available inside Moho | MohoMCP uses `io.open`, `os.remove`, `os.rename`, `os.getenv` and `print` |
| World frame spans y ∈ [-1, 1], x ∈ ±(width/height) | Lost Marble forum "Show pixel coordinates" (snippet; user-contributed) |
| A project-sized image fills the frame | Lost Marble forum "Scale layer to fit project?" (snippet: create the project at the image's exact pixel size, then import) |

## Members used

Abbreviations: **MS** mohoscripting.com, **SS/ST/MCP** repositories above,
**LMF** Lost Marble forum.

### ScriptInterface (`moho`)

| Member | Signature used | Evidence |
|---|---|---|
| `document` | field | SS, MCP |
| `CreateNewLayer` | `(layerType, undoable) -> MohoLayer` (inserted above the selected layer and selected) | MS `/methods/scriptinterface-createnewlayer`; ST `msSetGroupType.lua` |
| `PlaceLayerInGroup` | `(child, group, top, isUndoable)` | MS `/classes/scriptinterface`; ST `msSetGroupType.lua` |
| `LayerAsBone` / `LayerAsImage` / `LayerAsGroup` | cast a generic layer | SS, MCP, ST; MS `/methods/scriptinterface-layerasimage` (needed before image-specific calls) |
| `SetCurFrame`, `SetSelLayer`, `UpdateUI`, `AppVersion` | — | MCP; ST; SS |

### MohoDoc

| Member | Evidence |
|---|---|
| `Width()`, `Height()` (pixel size of the render output) | MS `/methods/mohodoc-height`; MCP |
| `Fps()`, `StartFrame()`, `EndFrame()` | MCP; MS `/methods/mohodoc-startframe` |
| `SetStartFrame(f)` | MS `/methods/mohodoc-setstartframe` |
| `SetEndFrame(f)` | MS `/classes/mohodoc` method table ("Sets the document's end frame") |
| `LayerByName(name)` | ST `msMatchSkeleton.lua`, `msFBFReorder.lua` |
| `CountLayers()`, `Layer(i)` (top level, 0-based; used by the probe) | MCP `tools/document.lua` |
| `SetDirty()` | SS; MCP |

### Layers

| Member | Evidence |
|---|---|
| `MohoLayer:SetName/Name/LayerType` | ST; MCP |
| `MohoLayer:SetLayerParentBone(id)` (-1 = none, -2 = flexi-binding) / `LayerParentBone()` | MS `/methods/moholayer-setlayerparentbone`, `/methods/moholayer-layerparentbone`; ST `msMatchSkeleton.lua` |
| `MohoLayer:IsGroupType()` (probe) | MCP `tools/layer.lua` |
| `MohoLayer:CountActions()`, `ActionName(i)`, `IsSmartBoneAction(name)` (probe, read-only) | MS `/classes/moholayer`, `/methods/moholayer-issmartboneaction`; ST `msPrintActions.lua` |
| `MohoImageLayer:SetSourceImage(path)` | LMF answer by Lost Marble: cast with `LayerAsImage` first (snippet); MS `layerasimage` page mentions it |
| `MohoGroupLayer:CountLayers()/Layer(i)` (0-based) | MCP `layer.getChildren`; ST `msRenameLayers.lua` |
| `MohoBoneLayer:Skeleton()` | MCP; ST |

### Skeleton and bones

| Member | Evidence |
|---|---|
| `M_Skeleton:AddBone(frame) -> M_Bone` | MS `/classes/m-skeleton`; SS |
| `M_Skeleton:BoneID(bone)`, `Bone(id)`, `CountBones()` | SS; ST; MCP |
| `M_Skeleton:UpdateBoneMatrix(id)` | SS |
| `M_Bone:SetName/Name` | SS; MCP |
| `M_Bone.fParent`, `fAnimParent` | SS (sets both) |
| `M_Bone.fPos`, `fAnimPos`, `fLength`, `fAngle`, `fAnimAngle` | SS; MCP; ST |
| `M_Bone.fMovedMatrix:Transform(vec)` (in place, bone-local → layer space) | SS |
| `AnimChannel:SetValue/GetValue/CountKeys/GetKeyWhen` (0-based key index) | SS; MCP |
| `LM.Vector2:new_local()`, `:Set(x, y)`, `.x/.y` | SS; MCP |

### Globals

| Member | Evidence |
|---|---|
| `MOHO.LT_BONE`, `MOHO.LT_IMAGE` | MS `/modules/MOHO` (LT_BONE = 4); ST; MCP. The script never hard-codes the values: the report records them at runtime |
| `MOHO.Localize`, `LM.GUI.Alert`, `LM.GUI.ALERT_INFO/ALERT_WARNING` | MCP `MohoMCP_Server.lua` |

## Key convention: child `fPos` lives in the parent's local frame

From `ss_make_bones.lua` (SimplSam): when chaining a new bone to `lastBone`,
the code sets `lastBoneTip:Set(newBoneLen, 0)` and then
`newBone.fPos:Set(lastBoneTip)`. A child sitting on its parent's tip therefore
has `fPos = (parentLength, 0)`. When the script shortens a bone it moves the
existing children with `fPos.x - xOffset`, which confirms that the origin is at
the parent's **base** and +x runs along the parent. It also transforms
parent-local points to layer space with `parentBone.fMovedMatrix:Transform(v)`.

`autorig.geometry.compute_rest_pose` implements exactly this. The native
report's `world_matrix` check is designed to catch it if Moho disagrees: it
compares Moho's own `fMovedMatrix` output with the art's joint positions.

## Deliberately not used

| Member | Reason |
|---|---|
| `FileSave`, `FileSaveAs`, `FileNew` | No documentation or code evidence found. The user creates the document and saves it manually. |
| `AnimChannel:SetKeyInterp` | Conflicting usage: MohoMCP passes a key **index**, Strider22 passes a **frame**. Moho's default interpolation is used. |
| `MohoImageLayer:SourceImage()` | Getter not confirmed. The report records the path that was set. |
| `MohoDoc:PrepUndo` | Exists (SimplSam), but undo of a multi-layer build is out of scope. Layers are created with `undoable=false`; to revert, close without saving. |
| `debug.getinfo` (script location) | MohoMCP shows it works in Moho 14, but absolute paths are baked in instead, which is simpler and explicit. |

## Evidence collected for Live Models (not yet allowlisted)

These are recorded for LM-1. They will be allowlisted only together with a mock
implementation and, ideally, a native experiment.

| Member | Evidence | Note |
|---|---|---|
| `MohoLayer:ActivateAction(name)` | MS `/classes/moholayer` ("Activates an action for editing"); ST `msCopyAction.lua` | Community code activates `"junk"` and then deletes it, which implies it creates missing actions |
| `MohoLayer:DeleteAction(name)` | ST `msCopyAction.lua` (commented-out code) | Weak |
| `MohoLayer:InsertAction(name, frame, byReference)` | MS `/methods/moholayer-insertaction`; ST `msCopyAction.lua` | — |
| `MohoLayer:CurrentAction()` | MS `/methods/moholayer-currentaction`; ST (commented code) | — |
| `MohoDoc:SetCurrentDocAction(nil)` | ST `msCopyAction.lua` (v10+) | Returns to the mainline |
| `AnimChannel:ActivateAction(name)` | MS `/methods/animchannel-activateaction` (v12, "creates action channel if not exist") | — |
| Moho 14.5 additions: `ImageLayer:BeginFacialTracking/EndFacialTracking`, `AudioLayer:AnalyzePhonemes`, `ScriptInterface:Play` | MS 14.5 features page (excerpt) | **No live-input assignment API was found** |

## Open questions for native verification

> **Native results (Phase 4.0, Moho 14.5.1, 2026-11-23; `docs/phase4_completion_report.md`):**
> - The child-`fPos` convention above is **confirmed**. Moho's own `fMovedMatrix` placed every bone's base and tip
>   where `compute_rest_pose` predicts (within 2e-3 units), including a child whose base is not on its parent's tip.
> - Question 2 is **answered yes**: `UpdateBoneMatrix` refreshed `fMovedMatrix` immediately.
> - Question 1, **partly**: `Layer(0)` is the first layer created, and the saved file keeps that order. The arm is
>   drawn over the body behind it. The frontmost layer is not yet confirmed visually.
> - Question 3, **open**: keys set with `SetValue` were saved with interpolation code `im: 1`, whose name is unconfirmed.
> - Every member called by the build script ran without error in Moho 14.5.1. The verify script's calls have not run
>   natively yet.

1. Does `PlaceLayerInGroup(..., top=true, ...)` stack each new layer on top?
   Expected: `forearm_art` is frontmost. The report records the child order;
   confirm visually.
2. Does `UpdateBoneMatrix(id)` refresh `fMovedMatrix` immediately? If not, the
   `world_matrix` check may fail even though the rig is correct. Compare the
   art against the bones visually.
3. Is the default interpolation smooth or linear? Either is acceptable; only
   the key frames are checked exactly.
4. Does `CreateNewLayer` on a document whose selected layer is inside a group
   behave the same? The script expects a fresh document (only "Layer 1").
