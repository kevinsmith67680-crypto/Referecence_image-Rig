"""Command line interface: ``python -m autorig <command>``.

Commands
--------
validate       Validate a rig spec (schema + semantics, optionally assets).
generate-art   Render the test character's PNG/SVG layers.
build-lua      Generate the Moho menu script with an absolute asset path baked in.
expected       Write the expected Moho-space rig values as JSON.
preview        Render the Python simulation (contact sheet + GIF).
verify-report  Check a report written by the Lua script inside Moho.
build-example  Regenerate every committed artefact of an example.
build-probe    Generate the read-only Moho API discovery script (Live Models research).
moho-file      Inspect / grep / diff .moho and .mohoproj files.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import __version__
from .artwork import generate_artwork
from .lua_codegen import PROBE_REPORT_FILENAME, PROBE_SCRIPT_FILENAME, SCRIPT_FILENAME, render_probe, write_lua
from .mohofile import MohoFileError, diff, grep, load_project, summarize
from .preview import render_contact_sheet, render_gif
from .report import expected_state, load_report, verify_report
from .spec import SpecError, load_spec, missing_assets


def _spec_dir(spec_path: str) -> Path:
    return Path(spec_path).resolve().parent


def cmd_validate(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    root = Path(args.asset_root) if args.asset_root else _spec_dir(args.spec)
    missing = missing_assets(spec, root)
    print(f"spec OK: {spec.character_id}, {len(spec.bones)} bones, {len(spec.parts)} parts, "
          f"{len(spec.tracks)} tracks, frames {spec.start_frame}..{spec.end_frame}")
    if missing:
        print(f"MISSING assets under {root}:")
        for m in missing:
            print(f"  - {m}")
        return 1
    print(f"assets OK under {root}")
    return 0


def cmd_generate_art(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    out = Path(args.out) if args.out else _spec_dir(args.spec)
    for p in generate_artwork(spec, out):
        print(f"wrote {p}")
    return 0


def cmd_build_lua(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    if args.reference:
        asset_root = ""
    else:
        root = Path(args.asset_root) if args.asset_root else _spec_dir(args.spec)
        root = root.resolve()
        missing = missing_assets(spec, root)
        if missing:
            print(f"error: assets missing under {root}: {missing}. Run generate-art first.", file=sys.stderr)
            return 1
        asset_root = root.as_posix()
    out = write_lua(spec, asset_root, args.out)
    print(f"wrote {out}")
    if asset_root:
        print(f"  asset root baked in: {asset_root}")
        print(f"  Moho will write its report to: {asset_root}/{spec.report_filename}")
    else:
        print("  reference build (no asset path): it will refuse to run inside Moho")
    return 0


def cmd_expected(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(expected_state(spec), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    root = _spec_dir(args.spec)
    out = Path(args.out) if args.out else root / "preview"
    print(f"wrote {render_contact_sheet(spec, root, out / 'contact_sheet.png')}")
    print(f"wrote {render_gif(spec, root, out / 'phase0_preview.gif')}")
    return 0


def cmd_verify_report(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    result = verify_report(spec, load_report(args.report))
    print(result.format())
    return 0 if result.passed else 1


def cmd_build_example(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    root = _spec_dir(args.spec)
    for p in generate_artwork(spec, root):
        print(f"wrote {p}")
    exp = root / "expected" / "expected_rig.json"
    exp.parent.mkdir(parents=True, exist_ok=True)
    exp.write_text(json.dumps(expected_state(spec), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {exp}")
    print(f"wrote {write_lua(spec, '', root / 'moho' / SCRIPT_FILENAME)}  (reference build)")
    print(f"wrote {render_contact_sheet(spec, root, root / 'preview' / 'contact_sheet.png')}")
    print(f"wrote {render_gif(spec, root, root / 'preview' / 'phase0_preview.gif')}")
    return 0


def cmd_build_probe(args: argparse.Namespace) -> int:
    report_dir = Path(args.report_dir).resolve()
    if not report_dir.is_dir():
        print(f"error: report directory does not exist: {report_dir}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_probe(report_dir.as_posix()), encoding="utf-8")
    print(f"wrote {out}")
    print(f"  Moho will write the probe report to: {report_dir.as_posix()}/{PROBE_REPORT_FILENAME}")
    return 0


def cmd_moho_file(args: argparse.Namespace) -> int:
    try:
        if args.moho_command == "inspect":
            project = load_project(args.path)
            if args.grep:
                hits = grep(project.data, args.grep)
                for path, value in hits[: args.max]:
                    print(f"{path} = {value}")
                print(f"({len(hits)} matches{'; truncated' if len(hits) > args.max else ''})")
            else:
                print("\n".join(summarize(project)))
            return 0
        a, b = load_project(args.before), load_project(args.after)
        ignore = re.compile(args.ignore, re.IGNORECASE) if args.ignore else None
        changes = diff(a.data, b.data, ignore=ignore)
        for c in changes[: args.max]:
            print(c.format())
        print(f"({len(changes)} changes{'; truncated' if len(changes) > args.max else ''})")
        return 0
    except (MohoFileError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autorig", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"autorig {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="validate a rig spec")
    p.add_argument("spec")
    p.add_argument("--asset-root", help="directory the spec's relative paths resolve against (default: spec dir)")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("generate-art", help="render the test character layers")
    p.add_argument("spec")
    p.add_argument("--out", help="output root (default: spec dir)")
    p.set_defaults(func=cmd_generate_art)

    p = sub.add_parser("build-lua", help="generate the Moho menu script")
    p.add_argument("spec")
    p.add_argument("--out", required=True, help=f"output .lua path (Moho expects the file name {SCRIPT_FILENAME})")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--asset-root", help="absolute asset directory on the Mac running Moho (default: spec dir)")
    g.add_argument("--reference", action="store_true", help="emit a reference build with no asset path")
    p.set_defaults(func=cmd_build_lua)

    p = sub.add_parser("expected", help="write expected Moho-space rig values")
    p.add_argument("spec")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_expected)

    p = sub.add_parser("preview", help="render the Python simulation preview")
    p.add_argument("spec")
    p.add_argument("--out", help="output directory (default: <spec dir>/preview)")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("verify-report", help="verify the JSON report written by Moho")
    p.add_argument("spec")
    p.add_argument("report")
    p.set_defaults(func=cmd_verify_report)

    p = sub.add_parser("build-example", help="regenerate all committed artefacts of an example")
    p.add_argument("spec")
    p.set_defaults(func=cmd_build_example)

    p = sub.add_parser("build-probe", help="generate the read-only Moho API discovery script")
    p.add_argument("--report-dir", required=True, help="existing directory on the Mac for the probe report")
    p.add_argument("--out", required=True, help=f"output .lua path (file name must be {PROBE_SCRIPT_FILENAME})")
    p.set_defaults(func=cmd_build_probe)

    p = sub.add_parser("moho-file", help="inspect, grep or diff .moho/.mohoproj files")
    msub = p.add_subparsers(dest="moho_command", required=True)
    q = msub.add_parser("inspect", help="summarize a project, or grep its JSON")
    q.add_argument("path")
    q.add_argument("--grep", help="case-insensitive regex matched against JSON keys and string values")
    q.add_argument("--max", type=int, default=200)
    q.set_defaults(func=cmd_moho_file)
    q = msub.add_parser("diff", help="structural JSON diff of two projects")
    q.add_argument("before")
    q.add_argument("after")
    q.add_argument("--ignore", help="regex of JSON paths to skip (e.g. volatile timestamps)")
    q.add_argument("--max", type=int, default=500)
    q.set_defaults(func=cmd_moho_file)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SpecError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
