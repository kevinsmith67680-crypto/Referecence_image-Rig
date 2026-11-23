#!/usr/bin/env bash
# AutoRig Studio - Phase 4.0 native Moho proof on this Mac (HARD GATE 4.0).
#
# Usage:
#   tools/macos/phase4_native_test.sh [--package DIR] [--side l|r] [--custom-content DIR]
#                                     [--moho-app PATH] [--no-cli-render] [--python PATH]
#
# It guides ONE native run and collects real evidence from the Moho application:
#   1. checks that this is macOS, finds Moho and reads its version (stops if absent)
#   2. builds the proof package (real artwork, shoulder + arm bones) with this
#      checkout's absolute paths baked into the two menu scripts
#   3. installs the scripts into <Custom Content Folder>/Scripts/Menu/AutoRig/
#   4. walks you through: new document, build script, Save As, Close, reopen
#      (the harness opens the saved file in Moho), verify script, Save, Quit
#   5. renders frames 1, 12 and 18 (tries Moho's command-line renderer, else asks
#      you to export them), then runs `python -m autorig native-verify`
# Evidence goes to docs/phase4/evidence/native_runs/<UTC time>_<character>_<side>/.
# Exit codes: 0 gate passed, 1 gate blocked, 2 not macOS, 3 Moho not found,
#             4 a Moho script failed, 5 bad arguments or environment.
# It never writes inside /Applications. Compatible with the stock macOS bash 3.2.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PACKAGE="$REPO/projects/schoolboy_tpose/character_package"
SIDE="l"
CUSTOM=""
MOHO_APP="${AUTORIG_MOHO_APP:-}"
CLI_RENDER=1
PYTHON="${PYTHON:-}"
CANVAS_W=1920
CANVAS_H=1080
SEARCH_DIRS="${AUTORIG_MOHO_SEARCH_DIRS:-/Applications:$HOME/Applications}"
OS_NAME="${AUTORIG_HARNESS_OS:-$(uname -s)}"   # test hook: lets the cloud suite exercise the checks

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --package) PACKAGE="${2:-}"; shift 2 ;;
    --side) SIDE="${2:-}"; shift 2 ;;
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
  echo "       Native validation cannot run here. In the cloud only the portable package is built:" >&2
  echo "         python -m autorig native-poc <package> --out <dir>" >&2
  echo "       On the Mac:  tools/macos/phase4_native_test.sh --custom-content \"<Moho Custom Content Folder>\"" >&2
  exit 2
fi

case "$SIDE" in l|r) ;; *) echo "error: --side must be l or r" >&2; exit 5 ;; esac

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
  echo "       HARD GATE 4.0 cannot be evaluated without the real application." >&2
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
WORK="$REPO/build/phase4_native/${CHAR}_${SIDE}"
EVID="$REPO/docs/phase4/evidence/native_runs/${STAMP}_${CHAR}_${SIDE}"
SAVED="$EVID/AutoRig_P4_Native.moho"
mkdir -p "$EVID/renders" "$EVID/screenshots"
exec > >(tee -a "$EVID/harness.log") 2>&1

echo "== AutoRig Phase 4.0 native proof"
echo "   Moho:      $MOHO_APP (version $MOHO_VERSION, build $MOHO_BUILD)"
echo "   macOS:     $(sw_vers -productVersion 2>/dev/null || echo unknown) ($(uname -m))"
echo "   package:   $PACKAGE (side $SIDE)"
echo "   work:      $WORK"
echo "   evidence:  $EVID"

"$PYTHON" - "$EVID/environment.json" "$MOHO_APP" "$MOHO_VERSION" "$MOHO_BUILD" "$MOHO_BIN" \
  "$(sw_vers -productVersion 2>/dev/null || echo unknown)" "$(uname -m)" "$OS_NAME" <<'PY'
import datetime, json, sys
out, app, ver, build, binary, macos, arch, osname = sys.argv[1:9]
json.dump({"harness": "tools/macos/phase4_native_test.sh", "os": osname, "macos_version": macos, "arch": arch,
           "moho_app": app, "moho_version": ver, "moho_build": build, "moho_binary": binary,
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
  for f in rig_spec.json native_manifest.json phase4_native_build_report.json phase4_native_verify_report.json; do
    if [ -f "$WORK/$f" ]; then cp -f "$WORK/$f" "$EVID/$f"; fi
  done
  echo
  echo "== Checking the evidence against HARD GATE 4.0"
  set +e
  "$PYTHON" -m autorig native-verify "$WORK" --evidence "$EVID"
  GATE=$?
  set -e
  echo
  echo "Evidence folder: $EVID"
  echo "Send it back by committing it on the working branch, e.g.:"
  echo "  git add \"docs/phase4/evidence/native_runs/$(basename "$EVID")\" && git commit -m \"Add Phase 4.0 native run evidence\" && git push"
  exit "$GATE"
}

# --- 3. package and scripts ----------------------------------------------------------------
echo
echo "== Building the proof package"
rm -f "$WORK"/phase4_native_build_report.json "$WORK"/phase4_native_verify_report.json
"$PYTHON" -m autorig native-poc "$PACKAGE" --out "$WORK" --side "$SIDE" --canvas "${CANVAS_W}x${CANVAS_H}" \
  --asset-root "$WORK"
BUILD_LUA="$WORK/moho/AutoRig_P4_NativeBuild.lua"
VERIFY_LUA="$WORK/moho/AutoRig_P4_NativeVerify.lua"

if [ -n "$CUSTOM" ]; then
  DEST="$CUSTOM/Scripts/Menu/AutoRig"
  mkdir -p "$DEST"
  cp -f "$BUILD_LUA" "$DEST/"
  cp -f "$VERIFY_LUA" "$DEST/"
  echo "Installed: $DEST/AutoRig_P4_NativeBuild.lua"
  echo "           $DEST/AutoRig_P4_NativeVerify.lua"
  observe scripts_installed "custom_content_folder"
else
  pause "In Moho: Scripts > Install Script... and install BOTH files:
      $BUILD_LUA
      $VERIFY_LUA"
  observe scripts_installed "install_script_menu"
fi

# --- 4. the native steps -----------------------------------------------------------------------
open -a "$MOHO_APP"
pause "Moho is starting. If it was already running, restart it so the new scripts load.
    Then: File > New, File > Project Settings > Dimensions ${CANVAS_W} x ${CANVAS_H},
    then Scripts > AutoRig > AutoRig Phase 4.0 native proof."
if ! wait_until_present "$WORK/phase4_native_build_report.json" "The build report"; then
  echo "error: no build report appeared at $WORK/phase4_native_build_report.json" >&2
  observe build_report "missing"
  finish
fi
BUILD_STATUS="$(report_status "$WORK/phase4_native_build_report.json")"
echo "Build script status: $BUILD_STATUS"
observe build_status "$BUILD_STATUS"
if [ "$BUILD_STATUS" != "completed" ]; then
  echo "error: the build script did not complete (see the errors above and the Lua Console)." >&2
  finish
fi

observe arm_moved_in_viewport_f12 "$(ask_yn "Go to frame 12 in the timeline. Is the arm artwork lowered, following the bone?")"

printf '%s' "$EVID" | pbcopy 2>/dev/null || true
pause "File > Save As... and save the document as:
      $SAVED
    Folder: THIS run's folder, $(basename "$EVID")
    (older run folders sit next to it in native_runs/ - do not pick one of those).
    The folder path is on the clipboard: in the save dialog press Cmd+Shift+G, paste it, press Enter,
    then name the file AutoRig_P4_Native.moho"
while [ ! -s "$SAVED" ]; do
  CAND="$(find "$REPO/docs/phase4/evidence/native_runs" "$REPO/build/phase4_native" -maxdepth 2 \
    -name 'AutoRig_P4_Native.moho' -newer "$RUN_MARK" 2>/dev/null | sed -n '1p' || true)"
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
open -a "$MOHO_APP" "$SAVED"
observe reopened_with_images "$(ask_yn "Did $SAVED reopen with the character's artwork visible?")"

pause "Scripts > AutoRig > AutoRig Phase 4.0: Verify Reopened Rig"
if ! wait_until_present "$WORK/phase4_native_verify_report.json" "The verify report"; then
  echo "error: no verify report appeared at $WORK/phase4_native_verify_report.json" >&2
  observe verify_report "missing"
  finish
fi
VERIFY_STATUS="$(report_status "$WORK/phase4_native_verify_report.json")"
echo "Verify script status: $VERIFY_STATUS"
observe verify_status "$VERIFY_STATUS"
if [ "$VERIFY_STATUS" != "completed" ]; then
  echo "error: the verify script did not complete." >&2
  finish
fi
observe arm_raised_in_viewport_f18 "$(ask_yn "Moho is now at frame 18. Is the arm raised?")"

pause "File > Save (Cmd+S), then quit Moho (Cmd+Q)."
while [ "$(mtime "$SAVED")" -le "$SAVED_MTIME" ]; do
  printf '\n    %s has not been saved again since the verify script ran.\n    Press Cmd+S in Moho, then press Enter to check again (or type s and Enter to skip): ' "$(basename "$SAVED")"
  read -r a || a="s"
  case "$a" in s|S) break ;; esac
done
if [ "$(mtime "$SAVED")" -le "$SAVED_MTIME" ]; then
  echo "warning: $SAVED was not re-saved after the verify script; frame 18 will render without the new key." >&2
  observe resaved_after_verify "no"
else
  observe resaved_after_verify "yes"
fi

# --- 5. renders --------------------------------------------------------------------------------
render_frame() {  # frame number -> renders/frame_000N.png
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
echo "== Rendering frames 1, 12 and 18"
for f in 1 12 18; do render_frame "$f"; done

pause "Optional: copy the Lua Console text into $EVID/lua_console.txt and put any screenshots of
    Moho (Cmd+Shift+4, Space, click the window) into $EVID/screenshots/."
finish
