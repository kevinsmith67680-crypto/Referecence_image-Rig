# Phase 0: install and run on macOS (Apple Silicon) with Moho Pro 14

This guide installs the **AutoRig Phase 0 menu script** into an existing
Moho Pro 14 installation and runs the native verification. It does not cover
installing Moho itself.

> Status: these steps were written in a cloud sandbox **without access to Moho
> or macOS**. Moho UI locations come from the Moho manual and community sources
> (see `docs/moho-lua-api-evidence.md`). Where a menu name may differ on your
> install, the guide says so. Please note any differences you find so the guide
> can be corrected.

## 0. Prerequisites

| Item | Notes |
|---|---|
| Moho **Pro** 14 (14.x) | The Scripts menu is a Pro feature (Moho manual, ch. 23 "Scripts Menu (Pro Only)"). Moho 14 embeds Lua 5.4. |
| macOS on the M1 Mac | Any version supported by Moho 14. |
| Python **3.12** | `brew install python@3.12` (Homebrew) or the python.org installer. Check with `python3.12 --version`. |
| Git | `xcode-select --install` provides it. |
| External SSD | Must be mounted whenever Moho opens the rig, because image layers reference absolute paths on it. |

## 1. Get the code onto the SSD

```bash
cd "/Volumes/<YourSSD>/<projects folder>"
```

```bash
git clone https://github.com/kevinsmith67680-crypto/Referecence_image-Rig.git
```

```bash
cd Referecence_image-Rig && git checkout claude/eloquent-cori-iv4ab1
```

Paths with spaces are fine. The generator escapes them, and the test suite runs
with a path containing a space and an accented character.

## 2. Python environment and cloud test suite (on the Mac)

```bash
python3.12 -m venv .venv
```

```bash
.venv/bin/pip install -e '.[dev]'
```

```bash
.venv/bin/python -m pytest
```

Expected: every test passes except **2 skipped**. The skipped ones are the native
Moho tests, which you run in step 7.

## 3. Find Moho's Custom Content Folder

Moho keeps user scripts in its **Custom Content Folder**. You pick it the first
time Moho starts, and it appears in Moho's preferences. Community install
guides also mention a File-menu command that opens it. Use whatever path your
Moho shows. If you are unsure, search for it:

```bash
find ~ -maxdepth 6 -type d -path "*Scripts/Menu" 2>/dev/null | grep -i moho
```

Do **not** install into `/Applications/Moho 14/...`. That is the signed app
bundle, and the installer refuses to write there.

## 4. Generate and install the menu script

```bash
tools/macos/install_phase0.sh --custom-content "/path/to/Moho Custom Content Folder"
```

This validates the spec and art, generates
`build/moho/AutoRig_Phase0_Build.lua` with the **absolute path of this
checkout's art** baked in, and copies it to
`<Custom Content>/Scripts/Menu/AutoRig/AutoRig_Phase0_Build.lua`. It also
installs **AutoRig Probe**, a read-only discovery script for the Live Models
research (`docs/live-models.md` §6). You don't need the probe for Phase 0.

Alternative: run the script without `--custom-content`, then in Moho use
**Scripts → Install Script…** (Moho manual, ch. 23 "Install Script") and pick
`build/moho/AutoRig_Phase0_Build.lua`.

Notes:

- The file name must stay `AutoRig_Phase0_Build.lua`, because it matches the
  script's internal `ScriptName`.
- If you move the repository or the SSD mounts under a different name, run the
  installer again. The path is baked in.
- `examples/phase0_test_character/moho/AutoRig_Phase0_Build.lua` is a
  **reference build without a path**. It is there for review only and refuses
  to run.

## 5. Load the script in Moho

Quit and restart Moho Pro 14. Restarting always reloads scripts. Community
sources also mention **Reload Tools and Brushes** (Alt+Shift+Ctrl+L on
Windows); the macOS shortcut is unverified.

You should now see **Scripts → AutoRig → AutoRig Phase 0: Build Test Rig**.

## 6. Run the build

1. **File → New** to create a fresh document.
2. Set the project dimensions to exactly **1920 × 1080** (File → Project
   Settings). The script refuses any other size, because the art is 1920 × 1080
   and has to fill the frame exactly.
3. Run **Scripts → AutoRig → AutoRig Phase 0: Build Test Rig**.
4. If macOS asks whether Moho may access files on a removable volume, click
   **Allow**. Without it the script cannot read the art or write its report on
   the SSD.
5. Expected result: an alert "AutoRig Phase 0: test rig built…", a Lua console
   with `[AutoRig]` log lines, a bone layer `AutoRig_Phase0` holding four image
   layers, and a timeline of 1–36. The script also writes
   `examples/phase0_test_character/phase0_moho_report.json` (git-ignored).
6. Save the document, for example
   `File → Save As… → build/phase0_test_rig.moho`.

If you get an alert saying **"nothing was changed"**, read its second line. It
reports one of: wrong project size, a layer `AutoRig_Phase0` already exists,
an image was not found, or a reference build is installed. Fix the cause and run
again on a new document.

## 7. Native verification

Automated comparison of Moho's read-back against the spec:

```bash
.venv/bin/python -m autorig verify-report examples/phase0_test_character/rig_spec.json examples/phase0_test_character/phase0_moho_report.json
```

Expected last line: `VERDICT: PASS (…)`. Then run the native pytest tests
(previously NOT EXECUTED):

```bash
AUTORIG_MOHO_REPORT=examples/phase0_test_character/phase0_moho_report.json .venv/bin/python -m pytest -m moho_native -v
```

Then work through the visual checks in
**[manual-verification.md](manual-verification.md)**.

## 8. Troubleshooting

| Symptom | Likely cause / action |
|---|---|
| No AutoRig entry in the Scripts menu | Wrong folder or file name; check the Lua console for load errors; restart Moho |
| "Project dimensions are WxH…" | Set the project to 1920 × 1080 and run on a new document |
| "Image not found" although the file exists | Removable-volume permission denied (System Settings → Privacy & Security → Files and Folders → Moho); the SSD is mounted under a different name (re-run the installer) |
| "build FAILED (Moho API error?)" | A Moho API assumption is wrong. **Close the document without saving.** Send the console text and `phase0_moho_report.json`, which names the failing call |
| `verify-report` fails on `world_matrix` only | Bones may not line up with the art, or `fMovedMatrix` was not refreshed. Compare visually (checklist M6) and send the report |
| `verify-report` fails on `angle_keys` | Moho stores keys differently than expected; send the report |
| No report file | The script could not write to the SSD folder (permission). The console shows "Could not write report" |

## 9. What to send back for the Phase 0 sign-off

1. The full output of `verify-report` and of the native pytest run.
2. `examples/phase0_test_character/phase0_moho_report.json`.
3. The Moho version (Help → About, and `moho_app_version` in the report).
4. Screenshots of frames 0, 10 and 28 with the bone layer selected, plus the
   Layers panel.
5. The completed checklist from `manual-verification.md`.
