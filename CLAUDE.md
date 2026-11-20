# CLAUDE.md — AutoRig Studio

AutoRig Studio converts a single cartoon character reference image into a
faithful, editable **Moho Pro 14** character rig.

## Current phase and gate

- **Phase 0 (Moho rigging proof of concept) is implemented but NOT locally
  verified.** Its cloud verdict is PARTIAL; see `docs/phase0/completion-report.md`.
- **Do not start Phase 1 (SAM 3 segmentation via Replicate) until the user
  reports that the native Moho checks in `docs/phase0/manual-verification.md`
  passed on their Mac.** If asked to start SAM 3 work earlier, point to this gate.

## Environment facts

- Development happens in a cloud sandbox (Claude Code on the web). **Moho is
  not available here.** The user runs Moho Pro 14 on an Apple M1 Mac, with the
  project stored on an external SSD. Paths can contain spaces (`/Volumes/...`).
- Python 3.12 backend (`src/autorig`). Moho 14 embeds **Lua 5.4**.
- `mohoscripting.com` and `lostmarble.com` are blocked from the sandbox; GitHub
  is reachable. Web search snippets were used as secondary evidence.

## Non-negotiable rules

1. **Never claim Moho behaviour was verified unless Moho actually ran.** Cloud
   results are "verified against the mock / in simulation". Native tests are
   reported as **NOT EXECUTED** until the user runs them.
2. **Never invent Moho API functions.** Generated Lua may only use members in
   `src/autorig/data/moho_api_allowlist.json`. Adding one requires an evidence
   entry (doc page, working third-party code, or forum answer) in the allowlist
   and in `docs/moho-lua-api-evidence.md`, and an implementation in
   `tests/lua/mock_moho.lua`. `tests/test_lua_moho_mock.py` enforces that the
   mock and the allowlist match exactly.
3. Geometry is computed in Python (`autorig.geometry`); the Lua template only
   applies precomputed numbers. Keep it that way so the maths stays unit-testable.
4. Do not modify the Moho application bundle (`/Applications/...`). Install
   scripts into Moho's Custom Content Folder.
5. Generated artefacts that are committed (`examples/*/art`, `expected/`,
   `moho/` reference script, `preview/`) must stay in sync with the generators.
   Regenerate them with `python -m autorig build-example <spec>`; tests fail otherwise.

## Commands

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest                       # full cloud suite (native tests skip)
.venv/bin/python -m autorig build-example examples/phase0_test_character/rig_spec.json
.venv/bin/python -m autorig build-lua examples/phase0_test_character/rig_spec.json --out build/moho/AutoRig_Phase0_Build.lua
.venv/bin/python -m autorig verify-report examples/phase0_test_character/rig_spec.json examples/phase0_test_character/phase0_moho_report.json
AUTORIG_MOHO_REPORT=examples/phase0_test_character/phase0_moho_report.json .venv/bin/python -m pytest -m moho_native -v
```

## Layout

- `src/autorig/` — spec (+ JSON Schema), geometry, artwork, Lua codegen, preview, report verifier, CLI
- `src/autorig/data/templates/phase0_build.lua` — the Moho menu-script template
- `tests/lua/mock_moho.lua` — strict mock of the allowlisted Moho API (Lua 5.4 via `lupa`)
- `examples/phase0_test_character/` — spec, art layers, reference script, previews, expected values
- `tools/macos/install_phase0.sh` — generates a machine-specific script and installs it
- `docs/` — architecture, Moho API evidence, MohoMCP assessment, Phase 0 guides and report

## Conventions

- Rig spec pixel coordinates: origin top-left, +y down. Moho world: origin at frame
  centre, +y up, frame height spans y ∈ [-1, 1].
- Bone `fPos` is in the parent's local frame (origin at parent base, +x along parent);
  `fAngle` is radians, CCW, parent-relative. Frame 0 = rest/setup pose.
- Commit messages: imperative mood; no model identifiers in commits or docs.
