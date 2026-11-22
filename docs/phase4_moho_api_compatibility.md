# Phase 4: Moho API compatibility

Target: Moho Pro 14 / 14.5 ("SHOWTIME"), embedded Lua 5.4. **No member below has been called in a running Moho.**
"Documentary" means it is in `src/autorig/data/moho_api_allowlist.json` with evidence: a documentation page, working
third-party code, or a forum answer. The strict mock implements exactly the allowlist. Every Phase 4.0 script runs
against it in the cloud suite, so a member outside the allowlist fails the tests.

## 1. Members used by the Phase 4.0 scripts

| member | build script | verify script | evidence | native |
|---|---|---|---|---|
| `ScriptInterface.document` | ✓ | ✓ | documentary | NOT EXECUTED |
| `ScriptInterface:CreateNewLayer(MOHO.LT_BONE / LT_IMAGE, false)` | ✓ | | documentary | NOT EXECUTED |
| `ScriptInterface:LayerAsBone / LayerAsImage / LayerAsGroup` | ✓ | ✓ (Bone, Group) | documentary | NOT EXECUTED |
| `ScriptInterface:PlaceLayerInGroup` | ✓ | | documentary | NOT EXECUTED |
| `ScriptInterface:SetCurFrame` | ✓ | ✓ | documentary | NOT EXECUTED |
| `ScriptInterface:SetSelLayer / UpdateUI / AppVersion` | ✓ | ✓ | documentary | NOT EXECUTED |
| `MohoDoc:Width / Height / Fps / StartFrame / EndFrame` | ✓ | ✓ | documentary | NOT EXECUTED |
| `MohoDoc:SetStartFrame / SetEndFrame / SetDirty` | ✓ | ✓ (SetDirty) | documentary | NOT EXECUTED |
| `MohoDoc:LayerByName` | ✓ (preflight) | ✓ (finds the rig) | documentary | NOT EXECUTED |
| `MohoLayer:SetName / Name / LayerType` | ✓ | ✓ (Name, LayerType) | documentary | NOT EXECUTED |
| `MohoLayer:SetLayerParentBone / LayerParentBone` | ✓ | ✓ (read) | documentary | NOT EXECUTED |
| `MohoGroupLayer:CountLayers / Layer` | ✓ (read-back) | ✓ | documentary | NOT EXECUTED |
| `MohoImageLayer:SetSourceImage` | ✓ | | documentary | NOT EXECUTED |
| `MohoBoneLayer:Skeleton` | ✓ | ✓ | documentary | NOT EXECUTED |
| `M_Skeleton:AddBone / BoneID / Bone / CountBones / UpdateBoneMatrix` | ✓ | ✓ (Bone, CountBones) | documentary | NOT EXECUTED |
| `M_Bone:SetName / Name`, fields `fParent fAnimParent fPos fAnimPos fLength fAngle fAnimAngle fMovedMatrix` | ✓ | ✓ (read; `fAnimAngle` written) | documentary | NOT EXECUTED |
| `AnimChannel:SetValue / GetValue / CountKeys / GetKeyWhen` | ✓ | ✓ | documentary | NOT EXECUTED |
| `LM.Vector2:new_local`, `:Set`, `.x .y`, `LM_Transform:Transform` | ✓ | ✓ | documentary | NOT EXECUTED |
| `MOHO.LT_BONE / LT_IMAGE / Localize`, `LM.GUI.Alert / ALERT_INFO / ALERT_WARNING` | ✓ | ✓ | documentary | NOT EXECUTED |

The verify script also reads the names on the `ScriptInterface` metatable whose names contain file, save, open, close,
new, revert, render, export or quit. This is plain Lua (`getmetatable`, `pairs`, `rawget`). Nothing it finds is called;
the report marks the list `called: false`. The same technique is used by the LM-0 probe.

## 2. Not allowlisted, so not called

| capability | candidate | evidence status | Phase 4.0 replacement |
|---|---|---|---|
| new document | `ScriptInterface:FileNew()` | forum script (secondary, from search snippets) | File > New |
| save | `ScriptInterface:FileSave()`, `FileSaveAs(path)` | mohoscripting.com listing (secondary, from search snippets) | File > Save As / Save |
| open / close | `FileOpen(path)`, `FileClose()` | forum script; a reported `FileClose()` bug with the Lua console open | close manually; the harness reopens with `open -a` |
| render | none known in Lua | — | command line (below) or File > Export Animation |
| interpolation | `AnimChannel:SetKeyInterp` | not evidenced for Moho 14 | not needed: every checked frame is a key frame |

Adoption rule: a candidate joins the allowlist only with an evidence entry in `docs/moho-lua-api-evidence.md` and a mock
implementation. Its presence in the verify report's `api_surface` counts as discovery evidence that the name exists. It
does not show what the member does.

## 3. Command-line rendering (macOS command, not a Lua API)

The harness tries, once per frame and with a 240 s limit:

```bash
"<Moho.app>/Contents/MacOS/<CFBundleExecutable>" -r "<saved>.moho" -f PNG -o "<dir>/frame.png" -start N -end N
```

Evidence: an Anime Studio-era forum post (`-r`, `-f`, `-o`, `-start`, `-end`; `-outfolder` from 9.5). It is not
confirmed for Moho 14.5. The command, its output and its exit code go to `renders/cli_render.log`. If no PNG appears, the
harness asks for File > Export Animation instead. Either way the PNG comes from Moho. Which route produced each frame is
recorded in `observations.json` (`render_f1` and so on).

## 4. File format

`src/autorig/mohofile.py` reads `.moho` as a zip holding one `.mohoproj` JSON document, or a bare `.mohoproj`. This
layout is not confirmed for Moho 14.5. The gate therefore treats parsing as supporting evidence: if the saved file does
not parse, the save still passes when the native verify script found the rig in the reopened file.
