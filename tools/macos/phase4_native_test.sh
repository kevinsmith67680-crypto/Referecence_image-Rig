#!/usr/bin/env bash
# AutoRig Studio - Phase 4 native Moho runs on this Mac (HARD GATE 4.0, Phase 4.1 and 4.2 gates).
#
# Usage:
#   tools/macos/phase4_native_test.sh [--phase 4.0|4.1|4.2] [--package DIR] [--side l|r] [--custom-content DIR]
#                                     [--moho-app PATH] [--no-cli-render] [--with-ik] [--review] [--python PATH]
#
# --phase 4.0 (default): the minimal proof (shoulder + arm on real artwork).
# --phase 4.1: the full-body rig (25 bones, every layer of the character, twelve test poses); --with-ik adds the
#              EXPERIMENTAL IK-target / joint-limit step on a copy of the verified rig.
# --phase 4.2: the hybrid rig (limb roots split at their pivots, arm chains above the torso, ten test poses, the
#              torso lean keyed after reopening); --review adds the pending clipped underlays (labelled); then the
#              optional manual Smart Bone test MT-1 on a copy, and your visual review.
# It guides ONE native run and collects real evidence from the Moho application:
#   1. checks that this is macOS, finds Moho and reads its version (stops if absent)
#   2. builds the package with this checkout's absolute paths baked into the menu scripts
#   3. installs the scripts into <Custom Content Folder>/Scripts/Menu/AutoRig/
#   4. walks you through: new document, build script, Save As, Close, reopen (the harness opens the saved file),
#      verify script, Save, Quit
#   5. renders the test frames (tries Moho's command-line renderer, else asks you to export them) and runs the
#      gate check (`autorig native-verify` / `native-fullbody-verify` / `native-hybrid-verify`)
# Evidence goes to docs/phase4/evidence/native_runs[_4_1|_4_2]/<UTC time>_<character>[_<side>]/.
# Exit codes: 0 gates passed, 1 gates blocked, 2 not macOS, 3 Moho not found,
#             4 a Moho script failed, 5 bad arguments or environment.
# It never writes inside /Applications. Compatible with the stock macOS bash 3.2.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PACKAGE="$REPO/projects/schoolboy_tpose/character_package"
SIDE="l"
PHASE="4.0"
WITH_IK=0
REVIEW=0
CUSTOM=""
MOHO_APP="${AUTORIG_MOHO_APP:-}"
CLI_RENDER=1
PYTHON="${PYTHON:-}"
CANVAS_W=1920
CANVAS_H=1080
SEARCH_DIRS="${AUTORIG_MOHO_SEARCH_DIRS:-/Applications:$HOME/Applications}"
OS_NAME="${AUTORIG_HARNESS_OS:-$(uname -s)}"   # test hook: lets the cloud suite exercise the checks

usage() {
  sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --package) PACKAGE="${2:-}"; shift 2 ;;
    --side) SIDE="${2:-}"; shift 2 ;;
    --phase) PHASE="${2:-}"; shift 2 ;;
    --with-ik) WITH_IK=1; shift ;;
    --review) REVIEW=1; shift ;;
    --custom-content) CUSTOM="${2:-}"; shift 2 ;;
    --moho-app) MOHO_APP="${2:-}"; shift 2 ;;
    --no-cli-render) CLI_RENDER=0; shift ;;
    --python) PYTHON="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown argument: $1" >&2; usage >&2; exit 5 ;;
  esac
done

# --- 1. platform and Moho --------------------------------------------------------------
if [ "$OS_NAME" != "Darwin" ]; then
  echo "error: this harness drives the Moho application and must run on macOS (found: $OS_NAME)." >&2
  echo "       Native validation cannot run here. In the cloud only the portable packages are built:" >&2
  echo "         python -m autorig native-poc <package> --out <dir>        (Phase 4.0)" >&2
  echo "         python -m autorig native-fullbody <package> --out <dir>   (Phase 4.1)" >&2
  echo "         python -m autorig native-hybrid <package> --out <dir>     (Phase 4.2)" >&2
  echo "       On the Mac:  tools/macos/phase4_native_test.sh --custom-content \"<Moho Custom Content Folder>\"" >&2
  exit 2
fi

case "$SIDE" in l|r) ;; *) echo "error: --side must be l or r" >&2; exit 5 ;; esac
case "$PHASE" in 4.0|4.1|4.2) ;; *) echo "error: --phase must be 4.0 or 4.1 or 4.2" >&2; exit 5 ;; esac
if [ "$WITH_IK" -eq 1 ] && [ "$PHASE" != "4.1" ]; then
  echo "error: --with-ik belongs to --phase 4.1" >&2
  exit 5
fi
if [ "$REVIEW" -eq 1 ] && [ "$PHASE" != "4.2" ]; then
  echo "error: --review belongs to --phase 4.2" >&2
  exit 5
fi

if [ -z "$MOHO_APP" ]; then
  FOUND=""
  OLDIFS="$IFS"; IFS=":"
  for d in $SEARCH_DIRS; do
    IFS="$OLDIFS"
    for app in "$d"/Moho*.app; do
      if [ -d "$app" ]; then
        FOUND="${FOUND}${app}
"
      fi
    done
    IFS=":"
  done
  IFS="$OLDIFS"
  MOHO_APP="$(printf '%s' "$FOUND" | sed -n '1p')"
  if [ "$(printf '%s' "$FOUND" | grep -c .)" -gt 1 ]; then
    echo "note: several Moho applications found; using the first (pass --moho-app to choose):"
    printf '%s' "$FOUND" | sed 's/^/        /'
  fi
fi
if [ -z "$MOHO_APP" ] || [ ! -d "$MOHO_APP" ]; then
  echo "error: Moho was not found (searched: $SEARCH_DIRS${MOHO_APP:+; given: $MOHO_APP})." >&2
  echo "       Install Moho Pro 14.5, or pass --moho-app \"/path/to/Moho.app\"." >&2
  echo "       The native gates cannot be evaluated without the real application." >&2
  exit 3
fi
case "$MOHO_APP" in
  *.app) ;;
  *) echo "error: --moho-app must be an application bundle (*.app): $MOHO_APP" >&2; exit 5 ;;
esac

PLIST="$MOHO_APP/Contents/Info.plist"
plist_get() {
  /usr/libexec/PlistBuddy -c "Print :$1" "$PLIST" 2>/dev/null || echo "unknown"
}
MOHO_VERSION="$(plist_get CFBundleShortVersionString)"
MOHO_BUILD="$(plist_get CFBundleVersion)"
MOHO_EXE="$(plist_get CFBundleExecutable)"
MOHO_BIN="$MOHO_APP/Contents/MacOS/$MOHO_EXE"

if [ -n "$CUSTOM" ]; then
  case "$CUSTOM" in
    /Applications/*|*.app/*|*.app)
      echo "error: refusing to write inside an application bundle ($CUSTOM)." >&2
      echo "       Use Moho's Custom Content Folder instead (see docs/phase0/install-macos-moho14.md)." >&2
      exit 5 ;;
  esac
  if [ ! -d "$CUSTOM" ]; then
    echo "error: Custom Content Folder not found: $CUSTOM" >&2
    exit 5
  fi
fi

# --- 2. Python ---------------------------------------------------------------------------
if [ -z "$PYTHON" ]; then
  if [ -x "$REPO/.venv/bin/python" ]; then
    PYTHON="$REPO/.venv/bin/python"
  elif command -v python3.12 >/dev/null 2>&1; then
    PYTHON="python3.12"
  else
    PYTHON="python3"
  fi
fi
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
  echo "error: $PYTHON is not Python >= 3.12. Create the venv first:" >&2
  echo "  python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 5
fi
export PYTHONPATH="$REPO/src${PYTHONPATH:+:$PYTHONPATH}"
if ! "$PYTHON" -c 'import PIL, numpy, jsonschema' 2>/dev/null; then
  echo "error: missing Python dependencies. Run:  $PYTHON -m pip install -e '.[dev]'" >&2
  exit 5
fi
if [ ! -f "$PACKAGE/artwork/manifest.json" ]; then
  echo "error: not a character package: $PACKAGE" >&2
  exit 5
fi
if [ ! -t 0 ]; then
  echo "error: this harness is interactive (it waits for you between Moho steps); run it in Terminal." >&2
  exit 5
fi

CHAR="$(basename "$(dirname "$PACKAGE")")"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
if [ "$PHASE" = "4.0" ]; then
  TITLE="AutoRig Phase 4.0 native proof"
  WORK="$REPO/build/phase4_native/${CHAR}_${SIDE}"
  RUNS="$REPO/docs/phase4/evidence/native_runs"
  EVID="$RUNS/${STAMP}_${CHAR}_${SIDE}"
  SAVED_NAME="AutoRig_P4_Native.moho"
  BUILD_REPORT="phase4_native_build_report.json"
  VERIFY_REPORT="phase4_native_verify_report.json"
  BUILD_MENU="AutoRig Phase 4.0 native proof"
  VERIFY_MENU="AutoRig Phase 4.0: Verify Reopened Rig"
  IK_MENU=""
  SCRIPTS="AutoRig_P4_NativeBuild.lua AutoRig_P4_NativeVerify.lua"
  COPY="rig_spec.json native_manifest.json $BUILD_REPORT $VERIFY_REPORT"
elif [ "$PHASE" = "4.2" ]; then
  TITLE="AutoRig Phase 4.2 hybrid rig"
  WORK="$REPO/build/phase4_2_native/${CHAR}"
  RUNS="$REPO/docs/phase4/evidence/native_runs_4_2"
  EVID="$RUNS/${STAMP}_${CHAR}"
  SAVED_NAME="AutoRig_P42_Hybrid.moho"
  BUILD_REPORT="phase4_2_build_report.json"
  VERIFY_REPORT="phase4_2_verify_report.json"
  BUILD_MENU="AutoRig Phase 4.2 hybrid rig"
  VERIFY_MENU="AutoRig Phase 4.2: Verify Reopened Hybrid Rig"
  IK_MENU=""
  SCRIPTS="AutoRig_P42_HybridBuild.lua AutoRig_P42_HybridVerify.lua"
  COPY="rig_spec.json native_hybrid_manifest.json native_skeleton.json binding_plan.json validation_plan.json"
  COPY="$COPY smartbone_plan.json artwork_requests.json manual_tests.json $BUILD_REPORT $VERIFY_REPORT"
else
  TITLE="AutoRig Phase 4.1 full-body rig"
  WORK="$REPO/build/phase4_1_native/${CHAR}"
  RUNS="$REPO/docs/phase4/evidence/native_runs_4_1"
  EVID="$RUNS/${STAMP}_${CHAR}"
  SAVED_NAME="AutoRig_P41_FullBody.moho"
  BUILD_REPORT="phase4_1_build_report.json"
  VERIFY_REPORT="phase4_1_verify_report.json"
  BUILD_MENU="AutoRig Phase 4.1 full-body rig"
  VERIFY_MENU="AutoRig Phase 4.1: Verify Reopened Full-Body Rig"
  IK_MENU="AutoRig Phase 4.1: IK Targets and Joint Limits (experimental)"
  SCRIPTS="AutoRig_P41_FullBodyBuild.lua AutoRig_P41_FullBodyVerify.lua AutoRig_P41_IKConstraints.lua"
  COPY="rig_spec.json native_fullbody_manifest.json native_skeleton.json binding_plan.json validation_plan.json"
  COPY="$COPY $BUILD_REPORT $VERIFY_REPORT phase4_1_ik_report.json"
fi
IK_REPORT="phase4_1_ik_report.json"
if [ -e "$EVID" ]; then
  echo "error: $EVID already exists; start again in a second (every run gets a new folder)." >&2
  exit 5
fi
SAVED="$EVID/$SAVED_NAME"
mkdir -p "$EVID/renders" "$EVID/screenshots"
exec > >(tee -a "$EVID/harness.log") 2>&1

echo "== $TITLE"
echo "   Moho:      $MOHO_APP (version $MOHO_VERSION, build $MOHO_BUILD)"
echo "   macOS:     $(sw_vers -productVersion 2>/dev/null || echo unknown) ($(uname -m))"
echo "   package:   $PACKAGE"
echo "   work:      $WORK"
echo "   evidence:  $EVID"

"$PYTHON" - "$EVID/environment.json" "$MOHO_APP" "$MOHO_VERSION" "$MOHO_BUILD" "$MOHO_BIN" \
  "$(sw_vers -productVersion 2>/dev/null || echo unknown)" "$(uname -m)" "$OS_NAME" "$PHASE" <<'PY'
import datetime, json, sys
out, app, ver, build, binary, macos, arch, osname, phase = sys.argv[1:10]
json.dump({"harness": "tools/macos/phase4_native_test.sh", "phase": phase, "os": osname, "macos_version": macos,
           "arch": arch, "moho_app": app, "moho_version": ver, "moho_build": build, "moho_binary": binary,
           "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()},
          open(out, "w"), indent=2)
PY

pause() {
  printf '\n>>> %s\n    Press Enter when done. ' "$1"
  read -r _ || true
}
ask_yn() {
  local a
  while true; do
    printf '%s [y/n] ' "$1" >&2
    read -r a || a=""
    case "$a" in
      y|Y|yes) echo "yes"; return 0 ;;
      n|N|no) echo "no"; return 0 ;;
    esac
  done
}
report_status() {
  "$PYTHON" -c 'import json,sys
r=json.load(open(sys.argv[1]))
print(r.get("status"))
for e in r.get("errors") or []: print("   ERROR:", e, file=sys.stderr)' "$1"
}
wait_for_report() {  # poll up to 60 s
  local path="$1" n=0
  while [ ! -s "$path" ]; do
    n=$((n + 1))
    if [ "$n" -gt 60 ]; then return 1; fi
    sleep 1
  done
  return 0
}
wait_until_present() {  # path label: keeps asking until the file exists or the user types q
  local path="$1" label="$2" a
  while true; do
    if wait_for_report "$path"; then return 0; fi
    printf '\n    %s has not appeared yet:\n      %s\n    Finish the step in Moho, then press Enter to check again (or type q and Enter to stop): ' \
      "$label" "$path"
    read -r a || a="q"
    case "$a" in q|Q) return 1 ;; esac
  done
}
mtime() {
  "$PYTHON" -c 'import os,sys; print(int(os.path.getmtime(sys.argv[1])) if os.path.exists(sys.argv[1]) else 0)' "$1"
}
moho_rss_kb() {  # resident memory of the running Moho process (performance report)
  local pid
  pid="$(pgrep -x "$MOHO_EXE" 2>/dev/null | sed -n '1p' || true)"
  if [ -n "$pid" ]; then ps -o rss= -p "$pid" 2>/dev/null | tr -d ' '; else echo "not_running"; fi
}
OBS_FILE="$(mktemp -t autorig_p4_obs.XXXXXX)"
RUN_MARK="$(mktemp -t autorig_p4_start.XXXXXX)"   # its mtime marks the start of this run
observe() {  # observe key value (values may contain spaces)
  printf '%s=%s\n' "$1" "$2" >> "$OBS_FILE"
}
finish() {
  "$PYTHON" - "$EVID/observations.json" "$OBS_FILE" <<'PY'
import datetime, json, sys
obs = dict(line.rstrip("\n").split("=", 1) for line in open(sys.argv[2], encoding="utf-8") if "=" in line)
obs["recorded_by"] = "the user, answering the harness prompts"
obs["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
json.dump(obs, open(sys.argv[1], "w"), indent=2)
PY
  for f in $COPY; do
    if [ -f "$WORK/$f" ]; then cp -f "$WORK/$f" "$EVID/$f"; fi
  done
  echo
  set +e
  if [ "$PHASE" = "4.0" ]; then
    echo "== Checking the evidence against HARD GATE 4.0"
    "$PYTHON" -m autorig native-verify "$WORK" --evidence "$EVID"
  elif [ "$PHASE" = "4.2" ]; then
    echo "== Checking the evidence against the Phase 4.2 gates"
    BEFORE="$(ls -d "$REPO"/docs/phase4/evidence/native_runs_4_1/*_"$CHAR" 2>/dev/null | sort | tail -n 1 || true)"
    if [ -n "$BEFORE" ] && [ -d "$BEFORE/renders" ]; then
      "$PYTHON" -m autorig native-hybrid-verify "$WORK" --evidence "$EVID" --character-package "$PACKAGE" \
        --before "$BEFORE"
    else
      "$PYTHON" -m autorig native-hybrid-verify "$WORK" --evidence "$EVID" --character-package "$PACKAGE"
    fi
  else
    echo "== Checking the evidence against the Phase 4.1 gates"
    "$PYTHON" -m autorig native-fullbody-verify "$WORK" --evidence "$EVID" --character-package "$PACKAGE"
  fi
  GATE=$?
  set -e
  echo
  echo "Evidence folder: $EVID"
  echo "Send it back by committing it on the working branch, e.g.:"
  echo "  git add \"${EVID#"$REPO"/}\" && git commit -m \"Add Phase $PHASE native run evidence\" && git push"
  exit "$GATE"
}

if [ "$PHASE" = "4.1" ] || [ "$PHASE" = "4.2" ]; then
  if ! grep -l '"passed": true' "$REPO"/docs/phase4/evidence/native_runs/*/gate_4_0.json >/dev/null 2>&1; then
    echo
    echo "NOTE: no Phase 4.0 run has passed HARD GATE 4.0 yet (docs/phase4/evidence/native_runs/*/gate_4_0.json)."
    echo "      The recommended order is: tools/macos/phase4_native_test.sh --phase 4.0 first."
    if [ "$(ask_yn "Run the Phase $PHASE test anyway (recorded as run before Phase 4.0 passed)?")" != "yes" ]; then
      echo "Stopped. Run --phase 4.0 first."
      observe phase4_0_passed_before "no"
      observe stopped "before Phase 4.0 passed"
      finish
    fi
    observe phase4_0_passed_before "no"
  else
    observe phase4_0_passed_before "yes"
  fi
fi

# --- 3. package and scripts ----------------------------------------------------------------
echo
echo "== Building the package"
for stale in "$BUILD_REPORT" "$VERIFY_REPORT" "$IK_REPORT" phase4_2_verify_report_smartbone.json; do
  rm -f "${WORK:?}/${stale:?}"
done
if [ "$PHASE" = "4.0" ]; then
  "$PYTHON" -m autorig native-poc "$PACKAGE" --out "$WORK" --side "$SIDE" --canvas "${CANVAS_W}x${CANVAS_H}" \
    --asset-root "$WORK"
elif [ "$PHASE" = "4.2" ]; then
  T0="$(date +%s)"
  if [ "$REVIEW" -eq 1 ]; then
    "$PYTHON" -m autorig native-hybrid "$PACKAGE" --out "$WORK" --asset-root "$WORK" --review
  else
    "$PYTHON" -m autorig native-hybrid "$PACKAGE" --out "$WORK" --asset-root "$WORK"
  fi
  observe package_build_seconds "$(( $(date +%s) - T0 ))"
  observe review_build "$([ "$REVIEW" -eq 1 ] && echo yes || echo no)"
  CANVAS_W="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["moho_canvas"][0])' "$WORK/native_hybrid_manifest.json")"
  CANVAS_H="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["moho_canvas"][1])' "$WORK/native_hybrid_manifest.json")"
else
  T0="$(date +%s)"
  "$PYTHON" -m autorig native-fullbody "$PACKAGE" --out "$WORK" --asset-root "$WORK"
  observe package_build_seconds "$(( $(date +%s) - T0 ))"
  CANVAS_W="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["moho_canvas"][0])' "$WORK/native_fullbody_manifest.json")"
  CANVAS_H="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["moho_canvas"][1])' "$WORK/native_fullbody_manifest.json")"
fi

if [ -n "$CUSTOM" ]; then
  DEST="$CUSTOM/Scripts/Menu/AutoRig"
  mkdir -p "$DEST"
  for sc in $SCRIPTS; do
    cp -f "$WORK/moho/$sc" "$DEST/"
    echo "Installed: $DEST/$sc"
  done
  observe scripts_installed "custom_content_folder"
else
  pause "In Moho: Scripts > Install Script... and install these files from $WORK/moho/:
      $SCRIPTS"
  observe scripts_installed "install_script_menu"
fi

# --- 4. the native steps -----------------------------------------------------------------------
open -a "$MOHO_APP"
pause "Moho is starting. If it was already running, restart it so the new scripts load.
    Then: File > New, File > Project Settings > Dimensions ${CANVAS_W} x ${CANVAS_H},
    then Scripts > AutoRig > $BUILD_MENU (wait for its alert before pressing Enter)."
if ! wait_until_present "$WORK/$BUILD_REPORT" "The build report"; then
  echo "error: no build report appeared at $WORK/$BUILD_REPORT" >&2
  observe build_report "missing"
  finish
fi
BUILD_STATUS="$(report_status "$WORK/$BUILD_REPORT")"
echo "Build script status: $BUILD_STATUS"
observe build_status "$BUILD_STATUS"
observe moho_rss_kb_after_build "$(moho_rss_kb)"
if [ "$BUILD_STATUS" != "completed" ]; then
  echo "error: the build script did not complete (see the errors above and the Lua Console)." >&2
  finish
fi

if [ "$PHASE" = "4.0" ]; then
  observe arm_moved_in_viewport_f12 "$(ask_yn "Go to frame 12 in the timeline. Is the arm artwork lowered, following the bone?")"
elif [ "$PHASE" = "4.2" ]; then
  observe arms_lowered_in_viewport_f10 "$(ask_yn "Go to frame 10. Are both arms lowered, their artwork following the bones?")"
  observe left_arm_raised_in_viewport_f20 "$(ask_yn "Frame 20: is the LEFT arm (image right) raised?")"
else
  observe left_arm_raised_in_viewport_f10 "$(ask_yn "Go to frame 10. Is the LEFT arm (image right) raised, its artwork following the bones?")"
  observe legs_move_in_viewport_f60_f70 "$(ask_yn "Frames 60 and 70: do the left leg (lifted) and the right knee (bent) move with their artwork?")"
fi

printf '%s' "$EVID" | pbcopy 2>/dev/null || true
pause "File > Save As... and save the document as:
      $SAVED
    Folder: THIS run's folder, $(basename "$EVID")
    (older run folders sit next to it - do not pick one of those).
    The folder path is on the clipboard: in the save dialog press Cmd+Shift+G, paste it, press Enter,
    then name the file $SAVED_NAME"
while [ ! -s "$SAVED" ]; do
  CAND="$(find "$RUNS" "$WORK" -maxdepth 2 -name "$SAVED_NAME" -newer "$RUN_MARK" 2>/dev/null | sed -n '1p' || true)"
  if [ -n "$CAND" ] && [ "$CAND" != "$SAVED" ]; then
    echo "The document was saved in another folder during this run:"
    echo "      $CAND"
    if [ "$(ask_yn "Move it into this run's folder ($(basename "$EVID"))?")" = "yes" ]; then
      mv "$CAND" "$SAVED"
      observe saved_relocated_from "$CAND"
      continue
    fi
  fi
  printf '\n    Not found at %s.\n    Save it there (File > Save As...), then press Enter to check again (or type q and Enter to stop): ' "$SAVED"
  read -r a || a="q"
  case "$a" in
    q|Q) observe saved "missing"; finish ;;
  esac
done
observe saved_bytes "$(wc -c < "$SAVED" | tr -d ' ')"
SAVED_MTIME="$(mtime "$SAVED")"

pause "File > Close (close the document, keep Moho open). Do not save again."
observe closed_before_reopen "$(ask_yn "Did the document close (no AutoRig rig visible in Moho)?")"

echo "Reopening the saved file in Moho..."
T_OPEN="$(date +%s)"
open -a "$MOHO_APP" "$SAVED"
observe reopened_with_images "$(ask_yn "Did $SAVED_NAME reopen with all of the character's artwork visible (no missing images)?")"
observe reopen_seconds_until_answer "$(( $(date +%s) - T_OPEN ))"
observe moho_rss_kb_after_reopen "$(moho_rss_kb)"

pause "Scripts > AutoRig > $VERIFY_MENU"
if ! wait_until_present "$WORK/$VERIFY_REPORT" "The verify report"; then
  echo "error: no verify report appeared at $WORK/$VERIFY_REPORT" >&2
  observe verify_report "missing"
  finish
fi
VERIFY_STATUS="$(report_status "$WORK/$VERIFY_REPORT")"
echo "Verify script status: $VERIFY_STATUS"
observe verify_status "$VERIFY_STATUS"
if [ "$VERIFY_STATUS" != "completed" ]; then
  echo "error: the verify script did not complete." >&2
  finish
fi
if [ "$PHASE" = "4.0" ]; then
  observe arm_raised_in_viewport_f18 "$(ask_yn "Moho is now at frame 18. Is the arm raised?")"
elif [ "$PHASE" = "4.2" ]; then
  observe torso_lean_in_viewport_f80 "$(ask_yn "Go to frame 80. Is the torso leaning (keys added after reopening)?")"
else
  observe pelvis_shift_in_viewport_f80 "$(ask_yn "Go to frame 80. Is the whole character shifted sideways (pelvis key added after reopening)?")"
fi

pause "File > Save (Cmd+S), then quit Moho (Cmd+Q)."
while [ "$(mtime "$SAVED")" -le "$SAVED_MTIME" ]; do
  printf '\n    %s has not been saved again since the verify script ran.\n    Press Cmd+S in Moho, then press Enter to check again (or type s and Enter to skip): ' "$SAVED_NAME"
  read -r a || a="s"
  case "$a" in s|S) break ;; esac
done
if [ "$(mtime "$SAVED")" -le "$SAVED_MTIME" ]; then
  echo "warning: $SAVED was not re-saved after the verify script; its new keys will not render." >&2
  observe resaved_after_verify "no"
else
  observe resaved_after_verify "yes"
fi

# --- 5. renders --------------------------------------------------------------------------------
copy_sequence() {  # dir first sub: copies the PNGs of dir (sorted) to $EVID/<sub>/frame_NNNN.png from frame first
  local dir="$1" first="$2" sub="$3" i=0 f png
  for png in $(ls "$dir"/*.png | sort); do
    f=$((first + i))
    cp -f "$png" "$EVID/$sub/$(printf 'frame_%04d.png' "$f")"
    i=$((i + 1))
  done
}
render_range() {  # doc first last sub: renders frames first..last of doc into $EVID/<sub>/frame_NNNN.png
  local doc="$1" first="$2" last="$3" sub="$4" dir n count T0
  mkdir -p "$EVID/$sub"
  count=$((last - first + 1))
  if [ "$CLI_RENDER" -eq 1 ] && [ -x "$MOHO_BIN" ]; then
    dir="$EVID/$sub/cli_${first}_${last}"
    mkdir -p "$dir"
    echo "-- command-line render of frames $first-$last of $(basename "$doc") (flags are secondary evidence)" \
      >> "$EVID/$sub/cli_render.log"
    T0="$(date +%s)"
    set +e
    perl -e 'alarm shift; exec @ARGV' 1800 "$MOHO_BIN" -r "$doc" -f PNG -o "$dir/frame.png" -start "$first" -end "$last" \
      >> "$EVID/$sub/cli_render.log" 2>&1
    echo "exit code: $?" >> "$EVID/$sub/cli_render.log"
    set -e
    observe "render_seconds_${sub}_${first}_${last}" "$(( $(date +%s) - T0 ))"
    n="$(ls "$dir"/*.png 2>/dev/null | wc -l | tr -d ' ')"
    if [ "$n" -eq "$count" ]; then
      copy_sequence "$dir" "$first" "$sub"
      observe "render_${sub}" "moho_command_line"
      return 0
    fi
    echo "note: the command line produced $n PNGs for $count frames; asking for a manual export." >&2
  fi
  dir="$EVID/$sub/manual_${first}_${last}"
  mkdir -p "$dir"
  pause "Render frames $first to $last from Moho: open $(basename "$doc"), File > Export Animation...,
      format PNG, start frame $first, end frame $last, output folder:
      $dir"
  n="$(ls "$dir"/*.png 2>/dev/null | wc -l | tr -d ' ')"
  if [ "$n" -eq "$count" ]; then
    copy_sequence "$dir" "$first" "$sub"
    observe "render_${sub}" "moho_export_animation"
  else
    echo "warning: found $n PNGs in $dir, expected $count" >&2
    observe "render_${sub}" "missing ($n of $count)"
  fi
}
render_frame() {  # frame number -> renders/frame_000N.png (Phase 4.0)
  local f="$1" dest name dir png
  name="$(printf 'frame_%04d.png' "$f")"
  dest="$EVID/renders/$name"
  if [ "$CLI_RENDER" -eq 1 ] && [ -x "$MOHO_BIN" ]; then
    dir="$EVID/renders/cli_$f"
    mkdir -p "$dir"
    echo "-- command-line render of frame $f (secondary evidence for the flags; result recorded)" \
      >> "$EVID/renders/cli_render.log"
    set +e
    perl -e 'alarm shift; exec @ARGV' 240 "$MOHO_BIN" -r "$SAVED" -f PNG -o "$dir/frame.png" -start "$f" -end "$f" \
      >> "$EVID/renders/cli_render.log" 2>&1
    echo "exit code: $?" >> "$EVID/renders/cli_render.log"
    set -e
    png="$(ls -t "$dir"/*.png 2>/dev/null | sed -n '1p' || true)"
    if [ -n "$png" ]; then
      cp -f "$png" "$dest"
      observe "render_f${f}" "moho_command_line"
      return 0
    fi
  fi
  mkdir -p "$EVID/renders/manual_$f"
  pause "Render frame $f from Moho: open $SAVED, File > Export Animation...,
      format PNG, start frame $f, end frame $f, output folder:
      $EVID/renders/manual_$f"
  png="$(ls -t "$EVID/renders/manual_$f"/*.png 2>/dev/null | sed -n '1p' || true)"
  if [ -n "$png" ]; then
    cp -f "$png" "$dest"
    observe "render_f${f}" "moho_export_animation"
  else
    echo "warning: no PNG found for frame $f" >&2
    observe "render_f${f}" "missing"
  fi
}
echo
if [ "$PHASE" = "4.0" ]; then
  echo "== Rendering frames 1, 12 and 18"
  for f in 1 12 18; do render_frame "$f"; done
elif [ "$PHASE" = "4.2" ]; then
  echo "== Rendering frames 1-90 (the ten test poses)"
  render_range "$SAVED" 1 90 renders
else
  echo "== Rendering frames 1-110 (the twelve test poses)"
  render_range "$SAVED" 1 110 renders
fi

# --- 6a. Phase 4.2 manual Smart Bone test (MT-1) and visual review ----------------------------------
if [ "$PHASE" = "4.2" ]; then
  SB_SAVED="$EVID/AutoRig_P42_Hybrid_SmartBone.moho"
  cp -f "$WORK/$VERIFY_REPORT" "$EVID/$VERIFY_REPORT" 2>/dev/null || true   # the main run's report, kept as is
  echo
  if [ "$(ask_yn "Run manual test MT-1 now (a Smart Bone switch for one gap fill, on a copy; about 15 minutes)?")" = "yes" ]; then
    rm -f "${WORK:?}/${VERIFY_REPORT:?}"
    open -a "$MOHO_APP" "$SAVED"
    pause "Follow MT-1 in $WORK/manual_tests.json (key PNGs in $WORK/correctives/, offsets in
      $WORK/smartbone_plan.json). Then File > Save As... $SB_SAVED, File > Close."
    if [ -s "$SB_SAVED" ]; then
      open -a "$MOHO_APP" "$SB_SAVED"
      pause "Scripts > AutoRig > $VERIFY_MENU (it lists the actions it finds), then File > Save and quit Moho."
      if wait_until_present "$WORK/$VERIFY_REPORT" "The verify report of the Smart Bone copy"; then
        cp -f "$WORK/$VERIFY_REPORT" "$EVID/phase4_2_verify_report_smartbone.json"
      fi
      cp -f "$EVID/$VERIFY_REPORT" "$WORK/$VERIFY_REPORT" 2>/dev/null || true  # finish copies the main report
      observe smart_bone_mt1_works "$(ask_yn "MT-1: did the fill appear under the joint while the bone turned, switch without popping, and vanish at rest?")"
      render_range "$SB_SAVED" 10 30 renders_smartbone
    else
      observe smart_bone_mt1 "copy not saved"
    fi
  else
    observe smart_bone_mt1 "not run"
  fi
  echo
  echo "Visual review (MT-4): look at the renders in $EVID/renders (frames 1, 10, ..., 90)."
  printf 'Do the shoulders, elbows, hips, knees and torso read as natural articulation? [accepted/rejected/later] '
  read -r a || a="later"
  case "$a" in
    accepted|rejected) observe visual_review_verdict "$a" ;;
    *) observe visual_review_verdict "not recorded" ;;
  esac
  printf 'Notes (one line, optional): '
  read -r a || a=""
  observe visual_review_notes "$a"
fi

# --- 6. Phase 4.1 experimental IK / joint limits ------------------------------------------------------
if [ "$WITH_IK" -eq 1 ]; then
  IK_SAVED="$EVID/AutoRig_P41_FullBody_IK.moho"
  echo
  echo "== EXPERIMENTAL: IK targets and joint limits (on a copy; the verified rig stays as it is)"
  open -a "$MOHO_APP" "$SAVED"
  pause "In Moho: File > Save As... $IK_SAVED  (a copy), then Scripts > AutoRig > $IK_MENU"
  if wait_until_present "$WORK/$IK_REPORT" "The IK report"; then
    observe ik_status "$(report_status "$WORK/$IK_REPORT")"
    observe ik_drag_arms "$(ask_yn "Drag LEFT_HAND_TARGET and RIGHT_HAND_TARGET with Manipulate Bones: does each arm follow its target without flipping, leaving the other arm alone?")"
    observe ik_drag_legs "$(ask_yn "Drag LEFT_FOOT_TARGET and RIGHT_FOOT_TARGET: does each leg follow without the knee flipping, leaving the other leg alone?")"
    observe constraint_drag_stops_at_limit "$(ask_yn "Rotate HEAD (or LEFT_FOREARM) past its limit with Manipulate Bones: does it stop at the limit?")"
    pause "Undo your test drags (Cmd+Z), File > Save (Cmd+S), then quit Moho (Cmd+Q)."
    if [ -s "$IK_SAVED" ]; then
      render_range "$IK_SAVED" 120 160 renders_ik
    else
      observe ik_saved "missing"
    fi
  else
    observe ik_status "missing"
  fi
fi

pause "Optional: copy the Lua Console text into $EVID/lua_console.txt and put any screenshots of
    Moho (Cmd+Shift+4, Space, click the window) into $EVID/screenshots/."
finish
