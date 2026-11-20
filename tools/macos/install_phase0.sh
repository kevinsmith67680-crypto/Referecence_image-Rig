#!/usr/bin/env bash
# AutoRig Studio - Phase 0: generate and install the Moho Pro 14 menu script on macOS.
#
# Usage:
#   tools/macos/install_phase0.sh                                  # generate only
#   tools/macos/install_phase0.sh --custom-content "<Moho Custom Content Folder>"
#
# What it does:
#   1. validates examples/phase0_test_character/rig_spec.json and its art
#   2. generates build/moho/AutoRig_Phase0_Build.lua with the ABSOLUTE path of
#      this checkout's example assets baked in (e.g. on your external SSD)
#   3. generates build/moho/AutoRig_Probe.lua (read-only Moho API discovery,
#      Live Models research) reporting into research/moho-14.5/
#   4. with --custom-content: copies both to <folder>/Scripts/Menu/AutoRig/
#
# It never writes inside /Applications (the signed Moho app bundle).
# Compatible with the stock macOS bash 3.2.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SPEC="$REPO/examples/phase0_test_character/rig_spec.json"
OUT="$REPO/build/moho/AutoRig_Phase0_Build.lua"
PROBE_OUT="$REPO/build/moho/AutoRig_Probe.lua"
PROBE_REPORT_DIR="$REPO/research/moho-14.5"
CUSTOM=""
PYTHON="${PYTHON:-}"

usage() {
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --custom-content) CUSTOM="${2:-}"; shift 2 ;;
    --python) PYTHON="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -n "$CUSTOM" ]; then
  case "$CUSTOM" in
    /Applications/*|*.app/*|*.app)
      echo "error: refusing to write inside an application bundle ($CUSTOM)." >&2
      echo "       Use Moho's Custom Content Folder instead (see docs/phase0/install-macos-moho14.md)." >&2
      exit 1 ;;
  esac
  if [ ! -d "$CUSTOM" ]; then
    echo "error: Custom Content Folder not found: $CUSTOM" >&2
    exit 1
  fi
fi

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
  exit 1
fi

export PYTHONPATH="$REPO/src${PYTHONPATH:+:$PYTHONPATH}"
if ! "$PYTHON" -c 'import PIL, jsonschema' 2>/dev/null; then
  echo "error: missing Python dependencies. Run:  $PYTHON -m pip install -e '.[dev]'" >&2
  exit 1
fi

echo "== Validating rig spec and assets"
"$PYTHON" -m autorig validate "$SPEC"

echo "== Generating Moho menu scripts"
"$PYTHON" -m autorig build-lua "$SPEC" --out "$OUT"
mkdir -p "$PROBE_REPORT_DIR"
"$PYTHON" -m autorig build-probe --report-dir "$PROBE_REPORT_DIR" --out "$PROBE_OUT"

if [ -z "$CUSTOM" ]; then
  echo
  echo "Generated: $OUT"
  echo "           $PROBE_OUT"
  echo "Not installed (no --custom-content given). Either:"
  echo "  a) In Moho: Scripts > Install Script... and pick the files above, or"
  echo "  b) re-run with --custom-content \"<your Moho Custom Content Folder>\""
  exit 0
fi

DEST_DIR="$CUSTOM/Scripts/Menu/AutoRig"
mkdir -p "$DEST_DIR"
cp -f "$OUT" "$DEST_DIR/AutoRig_Phase0_Build.lua"
cp -f "$PROBE_OUT" "$DEST_DIR/AutoRig_Probe.lua"
echo
echo "Installed: $DEST_DIR/AutoRig_Phase0_Build.lua"
echo "           $DEST_DIR/AutoRig_Probe.lua (read-only discovery, see docs/live-models.md)"
echo "Next: restart Moho Pro 14 (or reload tools/scripts), create a NEW 1920x1080 document,"
echo "      then run Scripts > AutoRig > AutoRig Phase 0: Build Test Rig"
