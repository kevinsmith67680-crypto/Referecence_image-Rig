# MohoMCP assessment (for AutoRig Studio)

Repository: <https://github.com/Kveto/MohoMCP> (MIT). Inspected at commit
`d592a57` ("Update docs: document full Windows + macOS cross-platform
support"). It has three commits in total.

## What it is

An MCP server for Moho Pro 14 with three parts:

1. **Lua plugin** (`moho-plugin/`): a menu script, `MohoMCP_Server.lua`, plus
   modules. It runs inside Moho and polls a temp directory
   (`$TMPDIR/moho-mcp/`) for JSON-RPC request files. Moho only runs Lua during
   UI callbacks, so the plugin wraps the `DrawMe` callbacks of the built-in tools
   and the menu script's `IsEnabled`. A "poller" tool has to be selected, and a
   keep-alive process (AppleScript on macOS) forces about 4 Hz repaints.
2. **Bridge** (`bridge/`, TypeScript/Node ≥ 18): exposes 26 MCP tools over stdio
   and exchanges `req_<id>.json` / `resp_<id>.json` files with the plugin.
3. **Static knowledge**: Moho 14 keyboard shortcuts and a tool reference.

## Tool coverage relevant to rigging

| Need in AutoRig | MohoMCP | Notes |
|---|---|---|
| Create bone layer, bones, image layers | **No** | No create tools for layers or bones |
| Bind layers to bones | **No** | No binding tool |
| Read the layer tree, bones, bone properties | Yes | `document_getLayers`, `layer_getBones`, `bone_getProperties` |
| Read and set keyframes and interpolation | Yes | `animation_getKeyframes`, `animation_setKeyframe`, `animation_setInterpolation` |
| Set bone and layer transforms per frame | Yes | `bone_setTransform`, `layer_setTransform` |
| Screenshot of the scene or UI | Yes | `document_screenshot` (scene via `FileRender`, UI via `screencapture`) |
| Save the document | No | — |

**Conclusion:** MohoMCP cannot build the Phase 0 rig. The rig is built by the
AutoRig-generated menu script, which is self-contained and has no runtime
dependency on MohoMCP. MohoMCP is useful for **verification and inspection**:
reading back the rig, scrubbing frames and taking screenshots for visual
diffing. That is optional in Phase 0 and a strong candidate for automating the
manual checklist later.

## Risks and caveats

- **Maturity:** a young, three-commit project. Its Lua handlers wrap almost
  every API call in `pcall` and probe several method names, which suggests
  uncertainty about the API. It is useful as evidence that a call exists (it is
  cited in our evidence table), not as an authority on semantics.
- **Installation writes into the app bundle:** `install-plugin.sh` copies into
  `/Applications/Moho 14/Moho Pro.app/Contents/Resources/Support/Scripts/...` and
  appends to `_tool_list.txt` there. That can need admin rights and can be lost
  when Moho updates. AutoRig's own installer uses the Custom Content Folder
  instead.
- **macOS permissions:** the input and screenshot tools need Accessibility (and
  possibly Screen Recording) permission for the terminal or Claude app.
- **Polling model:** commands only execute while Moho repaints, so latency is
  about 100–300 ms per round trip and it depends on the keep-alive process.
- **Not tested by this project:** nothing in MohoMCP was executed. The cloud
  sandbox has no Moho and no macOS.

## Optional Phase 0 cross-check with MohoMCP (Mac only, NOT EXECUTED)

After running the AutoRig script in Moho, with the MohoMCP server and poller
active and Claude Code connected through `.mcp.json`:

1. `document_getLayers`: expect a bone layer `AutoRig_Phase0` containing
   `torso_art`, `head_art`, `upper_arm_art` and `forearm_art`.
2. `layer_getBones` on that layer: expect `torso`, `head`, `upper_arm` and `forearm`.
3. `bone_getProperties` per bone: compare with
   `examples/phase0_test_character/expected/expected_rig.json`. MohoMCP reports
   angles in radians.
4. `animation_getKeyframes` on each bone angle channel: expect frames
   0, 1, 10, 19, 28, 36.
5. `document_setFrame` 10 and `document_screenshot` (`mode="scene"`): compare
   by eye with `preview/contact_sheet.png` (frame 10).

## Recommendation

Keep MohoMCP out of the Phase 0 critical path. Re-evaluate in Phase 2 for
automated visual regression testing, ideally after the project matures or
behind our own allowlist and mock discipline.
