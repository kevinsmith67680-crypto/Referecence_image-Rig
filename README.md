# AutoRig Studio

Turn a single cartoon character reference image into a faithful, **editable
Moho Pro 14 rig**: separated art layers, a bone hierarchy, layer-to-bone
bindings, ready to animate.

> **Status: Phase 2 (reconstruction and animation-ready assets): PARTIAL.** It is implemented and tested on a synthetic
> labelled character. Original pixels are provably untouched, and hidden regions are reconstructed, posed and QA-reported.
> Generative inpainting is untested (no Replicate credit), and no real artwork has been processed. See
> [docs/phase2_report.md](docs/phase2_report.md).
>
> **Phase 1 (AI character analysis and segmentation): PARTIAL.** The pipeline is implemented and
> tested on a synthetic ground-truth character. Real SAM 3 inference is blocked by Replicate credit (HTTP 402),
> and the real reference image is not yet in the repository. See [docs/phase1_report.md](docs/phase1_report.md).
>
> **Phase 0 (Moho rigging proof of concept): PARTIAL.**
> Everything that can run without Moho is implemented and passes in the cloud
> (282 tests across all phases). The native Moho run is **NOT EXECUTED** yet; it has to happen on
> the Mac. See [the completion report](docs/phase0/completion-report.md).

![Phase 0 test character, simulated](examples/phase0_test_character/preview/contact_sheet.png)

*Python simulation of the Phase 0 rig (layer binding + forward kinematics).
This is not a Moho render.*

## How Phase 0 works

```
rig_spec.json ──► autorig (Python 3.12) ──► AutoRig_Phase0_Build.lua ──► Moho Pro 14 (Lua 5.4)
  bones in px        geometry → Moho units      menu script, absolute        builds layers, bones,
  parts, z-order     art layers (PNG + SVG)     asset path baked in          bindings, 36-frame keys
  36-frame keys      expected values                                         ▼
                     preview (simulation)                          phase0_moho_report.json
                          ▲                                                  │ (read back from Moho)
                          └──────────── autorig verify-report ◄──────────────┘
```

- **4 transparent art layers** (`torso`, `head`, `upper_arm`, `forearm`), full-canvas 1920×1080 PNG plus SVG sources.
- **Rig spec** in JSON, validated by a JSON Schema and semantic checks.
- **Generated Lua menu script** that creates the bone layer, 4 bones (torso → head, torso → upper_arm → forearm), 4 image layers, the bindings and the animation keys.
- **36-frame test animation**: torso sway, head counter-tilt, two arm waves, loopable.
- **Verification loop**: the script reads the rig back from Moho and writes JSON, and `verify-report` checks it against the spec.
- **Strict mock Moho API**: cloud tests run the generated Lua under Lua 5.4 against a mock that rejects any API member without documented evidence.

## Quick start (cloud or Mac)

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

## Phase 1: analyse a reference image

```bash
export REPLICATE_API_TOKEN=...   # never commit it
.venv/bin/python -m autorig analyze path/to/reference.png --out character_projects/name --budget-usd 1.00
```

This writes `character_project/`: source copy, `analysis/*.json`, masks, original-pixel layers,
previews and `metadata.json`. To try it without paid inference, use the synthetic ground-truth character:
`--provider fixture` on `examples/phase1_synthetic_character/synthetic_fixture.png`.

![Phase 1 synthetic character segmentation](examples/phase1_synthetic_character/character_project/previews/segmentation.png)

*Synthetic test character (not a reference). Anatomical parts, joints and bones from the Phase 1 pipeline.*

## Phase 2: reconstruct hidden artwork

```bash
.venv/bin/python -m autorig prepare character_projects/name --out character_packages/name
.venv/bin/python -m autorig review character_packages/name --list
```

Open `character_packages/name/reports/visual_review.html` to inspect generated (magenta) regions and pose tests.

![Phase 2 pose contact sheet](examples/phase2_synthetic_character/character_package/reports/pose_contact_sheet.png)

*Synthetic test character posed by the standalone preview engine. Red = remaining gaps that the QA reports. This is not a Moho render.*

## Running it in Moho Pro 14 (Mac)

Read **[docs/phase0/install-macos-moho14.md](docs/phase0/install-macos-moho14.md)**. In short:

```bash
tools/macos/install_phase0.sh --custom-content "<your Moho Custom Content Folder>"
```

Restart Moho, create a new 1920×1080 document, then run **Scripts → AutoRig → AutoRig Phase 0: Build Test Rig**. After that:

```bash
.venv/bin/python -m autorig verify-report examples/phase0_test_character/rig_spec.json examples/phase0_test_character/phase0_moho_report.json
```

Then work through [docs/phase0/manual-verification.md](docs/phase0/manual-verification.md).

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Pipeline, components, coordinate systems, verification layers, roadmap |
| [docs/moho-lua-api-evidence.md](docs/moho-lua-api-evidence.md) | Every Moho API member used, with evidence and open questions |
| [docs/mohomcp-assessment.md](docs/mohomcp-assessment.md) | What MohoMCP can and cannot do for this project |
| [docs/live-models.md](docs/live-models.md) | Moho 14.5 Live Models: findings, unknowns, design, discovery protocol |
| [docs/phase1_report.md](docs/phase1_report.md) | Phase 1 results, Replicate usage and costs, accuracy, readiness, verdict |
| [docs/phase2_implementation_plan.md](docs/phase2_implementation_plan.md) | Phase 2 architecture, model selection, risks, acceptance tests |
| [docs/phase2_report.md](docs/phase2_report.md) | Phase 2 results: preservation, reconstruction vs GT, poses, readiness, verdict |
| [docs/real_artwork_test.md](docs/real_artwork_test.md) | First runs on the user's real character (front crop, then T-pose) |
| [docs/phase2_natural_pose_refinement.md](docs/phase2_natural_pose_refinement.md) | Natural posing: root causes, skinned joints, IK/balance, presets, before/after |
| [docs/phase0/install-macos-moho14.md](docs/phase0/install-macos-moho14.md) | Installation and run guide for macOS |
| [docs/phase0/manual-verification.md](docs/phase0/manual-verification.md) | Native checks to perform in Moho |
| [docs/phase0/completion-report.md](docs/phase0/completion-report.md) | Phase 0 results and verdict |
| [CLAUDE.md](CLAUDE.md) | Rules for AI-assisted development in this repo |

## Roadmap

| Phase | Scope | State |
|---|---|---|
| 0 | Moho rigging proof of concept (this repo) | Implemented; **awaiting local Moho verification** |
| LM | Moho 14.5 Live Models: Smart Bones and actions mapped to webcam and gamepad inputs | LM-0 (research and discovery tools) done; LM-1 blocked on Phase 0 and the Mac discovery data |
| 1 | AI character analysis: SAM 3 (Replicate) segmentation, anatomy, face, original-pixel layers, live-control contract | Implemented; **PARTIAL** (needs Replicate credit and the real reference image) |
| 2 | Occlusion analysis, pixel-preserving reconstruction, joint preparation, pose QA, live artwork manifest, review workflow | Implemented; **PARTIAL** (inpainting untested; synthetic evidence only) |
| 2+ | Joint/pivot estimation, hybrid bitmap/vector art, MohoMCP-driven verification | Not started |
