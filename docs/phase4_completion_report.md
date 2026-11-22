# Phase 4: completion report

| | |
|---|---|
| **Verdict** | **BLOCKED — NATIVE VALIDATION REQUIRED** |
| Phase reached | 4.0 (steps 1–4): portable package, Mac harness and gate checker built and cloud-tested |
| Native Moho run | **NOT EXECUTED**: no Mac is attached to this session, and Moho cannot run in the Linux cloud container |
| HARD GATE 4.0 | not passed: no evidence from the real application exists |
| Steps 5–23 (Phase 4.1+) | **not started**, as the gate requires |

## 1. Why it stops here

The request's hard gate says: *"If Moho cannot be run from the current environment, implement and test only the
portable package and local execution harness. Then stop with verdict: BLOCKED — NATIVE VALIDATION REQUIRED."*

- This session runs in a Linux container. Moho is a macOS application.
- No folder of the user's Mac is shared with the session, so no command can run on the Mac.
- `docs/phase4/evidence/native_runs/` is empty. No Moho-written report, saved document or render exists.
- So none of the five gate items can be met (see the table in §3).

## 2. What was built

| piece | file | tested in the cloud |
|---|---|---|
| integration audit | [`phase4_native_integration_audit.md`](phase4_native_integration_audit.md) | — |
| proof package generator (`autorig native-poc`) | `src/autorig/native/poc.py` | both characters: unscaled original pixels (byte-identical translation), bones from the package skeleton, split fidelity 0, arm fully on the canvas |
| build menu script | Phase 0 template under the name `AutoRig_P4_NativeBuild` (`lua_codegen.render_named_build_lua`) | strict mock; Phase 0 verifier 0 FAIL on both characters (*mock*) |
| verify-after-reopen menu script | `src/autorig/data/templates/phase4_native_verify.lua` | strict mock: read-back, keys preserved, new key at frame 18 read back; aborts cleanly without the rig and on a reference build |
| HARD GATE 4.0 checker (`autorig native-verify`) | `src/autorig/native/gate.py` | mock evidence gives MOCK_ONLY. A frozen arm, shifted art and a wrong aspect fail. Fixture inputs labelled native pass, which proves the acceptance path |
| Mac harness | `tools/macos/phase4_native_test.sh` | failure paths only: not macOS (exit 2), Moho missing (exit 3), app bundle as content folder, non-interactive (exit 5); bash 3.2 constructs; syntax |
| cloud check | `tools/phase4_cloud_check.py` → `docs/phase4/cloud_checks/` | both characters BLOCKED, as designed |
| docs | the seven `docs/phase4_*.md`; `docs/phase4/evidence/README.md` | — |

Tests: `tests/test_p4_native.py`, 23 tests. Full suite: 639 passed, 2 skipped (§5).

Inputs: the request asked for the actual PNGs if supplied. They were not (`/mnt/user-data/uploads` is empty), so the tough
student still uses the JPEG re-encodes, as in Phase 3.8.

## 3. HARD GATE 4.0 status

| requirement | status | what is missing |
|---|---|---|
| A real native Moho document has been created | NOT EXECUTED (MOCK_ONLY in the cloud) | the build report written by Moho |
| At least one bone genuinely moves artwork | NOT EXECUTED (MOCK_ONLY) | Moho renders of frames 1 and 12 |
| The document saves successfully | NOT EXECUTED | the saved `AutoRig_P4_Native.moho` |
| The document reopens successfully | NOT EXECUTED (MOCK_ONLY) | the verify report written by Moho on the reopened file |
| The bone remains functional after reopening | NOT EXECUTED (MOCK_ONLY) | that verify report, and the Moho render of frame 18 |

## 4. How to resume (exact command and user action)

On the M1 Mac, with Moho Pro 14.5 installed and this repository on the external SSD:

1. Get this branch:

   ```bash
   git fetch origin claude/eloquent-cori-iv4ab1 && git checkout claude/eloquent-cori-iv4ab1 && git pull
   ```

2. Once only, if the virtual environment does not exist yet:

   ```bash
   python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
   ```

3. Run the harness. It takes about 15 minutes and is interactive:

   ```bash
   tools/macos/phase4_native_test.sh --custom-content "<your Moho Custom Content Folder>"
   ```

   It opens Moho and asks you to:
   1. create a new 1920×1080 document and run **Scripts → AutoRig → AutoRig Phase 4.0 native proof**;
   2. **Save As** to the path it prints, then **Close**;
   3. let it reopen the file, then run **Scripts → AutoRig → AutoRig Phase 4.0: Verify Reopened Rig**;
   4. **Save**, then **Quit** Moho;
   5. let it render frames 1, 12 and 18. If Moho's command line does not render, it asks you to use File > Export
      Animation.

   It ends by printing the gate verdict.

4. Send the evidence back:

   ```bash
   git add docs/phase4/evidence/native_runs && git commit -m "Add Phase 4.0 native run evidence" && git push origin claude/eloquent-cori-iv4ab1
   ```

5. Tell me the evidence is pushed. I will re-run `autorig native-verify` on it. Phase 4.1 starts only if the result is
   `GATE 4.0 PASSED`; otherwise the failing check names what to fix.

Alternative to step 4: share the repository folder with a session through Remote Control (Claude desktop app →
Settings → Claude Code → Remote Control). The session can then read the evidence in place. The Moho steps themselves
still need you at the Mac.

## 5. Regression

`.venv/bin/python -m pytest` (full cloud suite) on commit 09df131: **639 passed, 2 skipped** (19 min).
- The 2 skips are the native Moho tests (`tests/native/test_moho_native.py`, NOT EXECUTED: no Moho report).
- No existing assertion was changed. Phase 3.8.1 ended at 616 passed; the 23 new tests are in
  `tests/test_p4_native.py`.
- No frontend file changed, so the editor build, vitest and e2e runs were not repeated.

## 6. What this phase does not claim

- No Moho behaviour is verified. Every report in `docs/phase4/cloud_checks/` was written by the strict mock, and every
  image there is a labelled simulation.
- No Moho screenshot or render was produced, edited or simulated as one.
- No pending artwork was approved. The proof uses original pixels only.
- Nothing is production-ready.
