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

Phase 1 (character analysis):
analyze           Reference image -> character_project/ (masks, layers, analysis, previews).
synthetic-character  Render the ground-truth test character (fixture, not a reference).
replicate-check   Compare the live vufinder/sam3 schema with the pinned snapshot (no inference, free).
replicate-smoke   One tiny budget-capped SAM 3 prediction to prove the integration end to end.
eval-synthetic    Score a project of the synthetic character against its ground truth.

Phase 2 (reconstruction & animation-ready assets):
prepare           Phase 1 project -> Phase 2 character package (occlusion, reconstruction, joints, poses, QA).
review            Inspect/apply human approval decisions for a Phase 2 package.
export-schemas    Write the Phase 1 JSON Schemas.
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


def _provider(args):
    if args.provider == "fixture":
        from .segmentation.sam3 import FixtureSegmentationProvider
        from .synthetic import concept_masks, render

        print("provider: fixture (synthetic ground-truth masks; only valid for the synthetic test character)")
        return FixtureSegmentationProvider(concept_masks(render()))
    from .segmentation.budget import CostPolicy
    from .segmentation.replicate_client import ReplicateClient
    from .segmentation.sam3 import PINNED_VERSION, Sam3ReplicateProvider

    policy = CostPolicy.from_env(ceiling_usd=args.budget_usd)
    prov = Sam3ReplicateProvider(ReplicateClient(), args.cache_dir, version=args.version or PINNED_VERSION,
                                 policy=policy)
    print(f"provider: replicate vufinder/sam3@{prov.version[:12]} | ceiling ${policy.ceiling_usd:.2f} | "
          f"spent so far ${prov.ledger.total_usd():.4f} (estimated)")
    return prov


def cmd_analyze(args: argparse.Namespace) -> int:
    from .phase1.project import AnalysisConfig, ProjectExistsError, analyze_character
    from .segmentation.budget import BudgetExceeded
    from .segmentation.replicate_client import ReplicateError

    cfg = AnalysisConfig(character_id=args.character_id, manual_landmarks=args.manual_landmarks,
                         overwrite=args.overwrite, min_score=args.min_score)
    try:
        res = analyze_character(args.image, args.out, _provider(args), cfg)
    except (ProjectExistsError, BudgetExceeded, ReplicateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    m = res.metadata
    f = m["fidelity"]
    print(f"wrote {res.root}")
    print(f"  schemas: {'all valid' if res.valid else res.schema_errors}")
    print(f"  coverage {f['coverage_ratio']}, overlaps {f['overlap_pixels']}, max error inside layers "
          f"{f['max_abs_error_inside_layers']}, linework retained {f['linework_retained_ratio']}")
    print(f"  estimated cost this run: ${m['cost']['this_run_estimated_usd']:.4f}")
    for w in m["warnings"]:
        print(f"  warning: {w}")
    return 0 if res.valid else 3


def cmd_synthetic(args: argparse.Namespace) -> int:
    from .synthetic import render

    ch = render()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    ch.image.save(out)
    gt = {"note": "SYNTHETIC test fixture with exact ground truth - not a user reference",
          "size": list(ch.size), "joints": ch.joints, "landmarks": ch.landmarks,
          "side_convention": "character's left (_l) on the image right"}
    gt_path = out.with_suffix(".ground_truth.json")
    gt_path.write_text(json.dumps(gt, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}\nwrote {gt_path}")
    return 0


def cmd_replicate_check(args: argparse.Namespace) -> int:
    from .segmentation.replicate_client import ReplicateClient, ReplicateError
    from .segmentation.sam3 import MODEL, PINNED_VERSION, load_version_snapshot

    snap = load_version_snapshot()
    owner, name = MODEL.split("/")
    try:
        with ReplicateClient() as c:
            live = {v["id"]: sorted(v["openapi_schema"]["components"]["schemas"]["Input"]["properties"])
                    for v in c.list_versions(owner, name)}
    except ReplicateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    known = {v["id"]: sorted(v["input"]) for v in snap["versions"]}
    print(f"pinned: {PINNED_VERSION}")
    ok = True
    for vid, inputs in live.items():
        state = "unchanged" if known.get(vid) == inputs else ("NEW VERSION" if vid not in known else "SCHEMA CHANGED")
        ok &= state == "unchanged"
        print(f"  {vid[:12]} {state}: {inputs}")
    if PINNED_VERSION not in live:
        print("  PINNED VERSION NO LONGER LISTED")
        ok = False
    return 0 if ok else 4


def cmd_replicate_smoke(args: argparse.Namespace) -> int:
    """Smallest possible paid check: one prediction, one text prompt, under a hard ceiling."""
    from .segmentation.budget import BudgetExceeded, CostPolicy
    from .segmentation.replicate_client import ReplicateClient, ReplicateError
    from .segmentation.sam3 import Sam3ReplicateProvider
    from .segmentation.types import Prompt

    prov = Sam3ReplicateProvider(ReplicateClient(), args.cache_dir, policy=CostPolicy.from_env(ceiling_usd=args.budget_usd))
    try:
        run = prov.segment(args.image, [Prompt(text=args.text, label=args.text)])
    except (BudgetExceeded, ReplicateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    inst = run.results[0].instances
    print(f"prediction {run.provenance.get('prediction_id')} cache_hit={run.provenance.get('cache_hit')} "
          f"predict_time={run.provenance.get('predict_time_s')}s est=${run.provenance.get('estimated_usd', 0):.4f}")
    print(f"'{args.text}': {len(inst)} instance(s) {[round(i.score, 3) for i in inst]} areas {[i.area for i in inst]}")
    print(f"ledger total ${prov.ledger.total_usd():.4f} of ${prov.policy.ceiling_usd:.2f}")
    return 0


def cmd_eval_synthetic(args: argparse.Namespace) -> int:
    from .phase1.evaluate import evaluate_synthetic

    r = evaluate_synthetic(args.project)
    print(json.dumps({k: r[k] for k in ("provider", "joint_error_px", "body_part_iou_mean", "fidelity")}, indent=2))
    print(f"wrote {Path(args.project) / 'evaluation_synthetic.json'}")
    return 0


def cmd_prepare(args: argparse.Namespace) -> int:
    from .phase1 import masks as M
    from .phase2.pipeline import PackageError, Phase2Config, prepare_character
    from .phase2.reconstruct import ReconstructionConfig
    from .segmentation.budget import BudgetExceeded
    from .segmentation.replicate_client import ReplicateError

    overrides = json.loads(Path(args.overrides).read_text()) if args.overrides else {}
    if "region_masks" in overrides:
        overrides["region_masks"] = {k: M.load_mask(v) for k, v in overrides["region_masks"].items()}
    provider = None
    if args.provider != "none":
        from .phase2.providers import ReplicateInpaintProvider
        from .segmentation.budget import CostPolicy
        from .segmentation.replicate_client import ReplicateClient

        provider = ReplicateInpaintProvider(ReplicateClient(), args.cache_dir, adapter=args.provider,
                                            policy=CostPolicy.from_env(ceiling_usd=args.budget_usd))
        print(f"reconstruction provider: {provider.name} (UNTESTED model; ceiling ${provider.policy.ceiling_usd:.2f})")
    cfg = Phase2Config(recon=ReconstructionConfig(n_candidates=args.candidates), sweep_step=args.sweep_step,
                       overrides=overrides, overwrite=args.overwrite, synthetic_ground_truth=args.synthetic_gt)
    try:
        res = prepare_character(args.phase1_project, args.out, provider, cfg)
    except (PackageError, BudgetExceeded, ReplicateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    q = res["docs"]["reconstruction_quality"]
    pres = q["original_pixel_preservation"]
    print(f"wrote {res['root']}  (valid={res['valid']})")
    print(f"  originals exact in neutral: {pres['neutral_originals_exact']}; generated pixels visible in neutral: "
          f"{pres['generated_pixels_visible_in_neutral']}")
    print(f"  generated pixels: {q['reconstruction_size']['generated_pixels_total']} (all pending review); "
          f"flags for review: {len(q['flags_for_human_review'])}")
    print(f"  review page: {res['root'] / 'reports' / 'visual_review.html'}")
    return 0 if res["valid"] else 3


def cmd_review(args: argparse.Namespace) -> int:
    from .phase2 import review as R

    root = Path(args.package)
    path = root / "reports" / "approvals.json"
    done = 0
    for item in args.approve or []:
        R.apply_decision(root, item, "approve"); done += 1
    for item in args.reject or []:
        R.apply_decision(root, item, "reject"); done += 1
    for item in args.request_candidate or []:
        R.apply_decision(root, item, "request_candidate"); done += 1
    for comp in args.restore_original or []:
        _restore(root, comp)
        done += 1
    if args.set_pivot:
        joint, x, y = args.set_pivot
        R.apply_decision(root, f"pivot:{joint}", "set_pivot", xy=[float(x), float(y)])
        ov_path = root / "reports" / "overrides.json"
        ov = json.loads(ov_path.read_text()) if ov_path.is_file() else {}
        ov.setdefault("pivots", {})[joint] = [float(x), float(y)]
        ov_path.write_text(json.dumps(ov, indent=2) + "\n")
        print(f"pivot override saved to {ov_path}; re-run `autorig prepare ... --overrides {ov_path} --overwrite`")
        done += 1
    if args.import_decisions:
        for d in json.loads(Path(args.import_decisions).read_text())["decisions"]:
            R.apply_decision(root, d["item"], d["decision"]); done += 1
    if args.approve_phase:
        try:
            print(R.approve_phase(root))
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
    doc = json.loads(path.read_text())
    counts = {}
    for i in doc["items"]:
        counts[i["status"]] = counts.get(i["status"], 0) + 1
    print(f"{done} decision(s) applied; item status counts: {counts}")
    if args.list:
        for i in doc["items"]:
            print(f"  {i['status']:<20} {i['type']:<24} {i['id']}")
    return 0


def _restore(root: Path, component: str) -> None:
    from .phase2 import review as R

    path = root / "reports" / "approvals.json"
    doc = json.loads(path.read_text())
    doc["items"].append({"id": component, "type": "restore_request", "status": "pending_review"})
    path.write_text(json.dumps(doc, indent=2) + "\n")
    R.apply_decision(root, component, "restore_original")


def cmd_export_schemas(args: argparse.Namespace) -> int:
    from .phase1.schemas import export
    from .phase2 import schemas as s2

    for p in export(args.out):
        print(f"wrote {p}")
    for name, schema in s2.SCHEMAS.items():
        p = Path(args.out) / f"{name}.schema.json"
        p.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {p}")
    return 0


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

    def replicate_opts(p):
        p.add_argument("--cache-dir", default=".autorig_cache/replicate", help="prediction cache + spend ledger")
        p.add_argument("--budget-usd", type=float, default=None,
                       help="cumulative spending ceiling (default: AUTORIG_REPLICATE_BUDGET_USD or $1.00)")

    p = sub.add_parser("analyze", help="Phase 1: analyse a reference image into a character project")
    p.add_argument("image")
    p.add_argument("--out", required=True)
    p.add_argument("--provider", choices=["replicate", "fixture"], default="replicate")
    p.add_argument("--version", help="override the pinned vufinder/sam3 version (must be in the snapshot)")
    p.add_argument("--character-id", default="character")
    p.add_argument("--manual-landmarks", help="JSON {\"joints\": {name: [x, y]}} overriding estimated joints")
    p.add_argument("--min-score", type=float, default=0.5)
    p.add_argument("--overwrite", action="store_true", help="replace an existing AutoRig project at --out")
    replicate_opts(p)
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("synthetic-character", help="render the synthetic ground-truth test character")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_synthetic)

    p = sub.add_parser("replicate-check", help="compare live vufinder/sam3 versions with the pinned snapshot")
    p.set_defaults(func=cmd_replicate_check)

    p = sub.add_parser("replicate-smoke", help="one tiny budget-capped SAM 3 prediction")
    p.add_argument("image")
    p.add_argument("--text", default="hand")
    replicate_opts(p)
    p.set_defaults(func=cmd_replicate_smoke)

    p = sub.add_parser("eval-synthetic", help="score a project of the synthetic character against ground truth")
    p.add_argument("project")
    p.set_defaults(func=cmd_eval_synthetic)

    p = sub.add_parser("prepare", help="Phase 2: build an animation-ready character package")
    p.add_argument("phase1_project")
    p.add_argument("--out", required=True)
    p.add_argument("--provider", default="none", choices=["none", "flux-fill-dev", "flux-fill-pro", "ideogram-v2"],
                   help="generative inpainting for non-flat regions (UNTESTED; default: deterministic only)")
    p.add_argument("--candidates", type=int, default=2)
    p.add_argument("--sweep-step", type=int, default=30)
    p.add_argument("--overrides", help="JSON with edges / exclude_regions / region_masks / pivots overrides")
    p.add_argument("--synthetic-gt", action="store_true", help="score against the synthetic fixture's ground truth")
    p.add_argument("--overwrite", action="store_true")
    replicate_opts(p)
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("pose-refine", help="Phase 2 refinement: natural pose presets with skinned joints")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("--presets", help="comma-separated preset names (default: all)")
    p.add_argument("--hand-sources", help="JSON naming hand drawings on the user's sheet (corrective hand variants)")
    p.set_defaults(func=cmd_pose_refine)

    p = sub.add_parser("editor", help="Phase 3: interactive artwork editor (FastAPI + React) for a package")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_editor)

    p = sub.add_parser("export-assets", help="Phase 3: export the Moho-ready asset package with quality gates")
    p.add_argument("package", help="Phase 2 character package (with editor/ edits, if any)")
    p.add_argument("--out", help="output directory (default <package>/editor/exports/<timestamp>)")
    p.set_defaults(func=cmd_export_assets)

    p = sub.add_parser("review", help="approve/reject Phase 2 generated assets")
    p.add_argument("package")
    p.add_argument("--list", action="store_true")
    p.add_argument("--approve", action="append")
    p.add_argument("--reject", action="append")
    p.add_argument("--request-candidate", action="append")
    p.add_argument("--restore-original", action="append", metavar="COMPONENT")
    p.add_argument("--set-pivot", nargs=3, metavar=("JOINT", "X", "Y"))
    p.add_argument("--import-decisions", metavar="DECISIONS_JSON")
    p.add_argument("--approve-phase", action="store_true")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("export-schemas", help="write Phase 1 JSON Schemas")
    p.add_argument("--out", default="src/autorig/data/schemas")
    p.set_defaults(func=cmd_export_schemas)
    return parser


def cmd_pose_refine(args: argparse.Namespace) -> int:
    from .phase2.natural import refine_package
    from .phase2.posing import PRESET_BY_NAME

    presets = [PRESET_BY_NAME[n] for n in args.presets.split(",")] if args.presets else None
    res = refine_package(args.package, presets=presets, hand_sources=args.hand_sources)
    rep = res["report"]
    print(f"wrote {res['out']}  (neutral pose exact: {rep['neutral_pose_exact']}, {rep['duration_s']} s)")
    for name, v in rep["presets"].items():
        print(f"  {name:24s} {v['status']:18s} confidence {v['confidence']:.2f}  defects {len(v['defects'])}")
    print("  sequences: " + ", ".join(f"{k} foot drift {v['max_planted_foot_drift_px']} px"
                                      for k, v in rep["sequences"].items()))
    print("  preview renders only - not Moho; nothing human-reviewed")
    return 0


def cmd_editor(args: argparse.Namespace) -> int:
    import uvicorn

    from .editor.server import create_app

    static = Path(__file__).resolve().parents[2] / "editor" / "dist"
    app = create_app(args.package, static if static.is_dir() else None)
    if not static.is_dir():
        print("note: editor/dist not built; API only (cd editor && npm install && npm run build)")
    print(f"AutoRig editor: http://{args.host}:{args.port}/   package: {args.package}")
    print("edits are saved to <package>/editor/ (the package's original artwork is never modified)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_export_assets(args: argparse.Namespace) -> int:
    from .editor.artwork import ArtworkModel
    from .editor.export import export_package
    from .editor.project import EditorProject

    try:
        r = export_package(EditorProject(args.package), ArtworkModel(Path(args.package)), args.out)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {r['out']}  overall {r['gates']['overall']}  production_ready {r['manifest']['production_ready']}")
    for g in r["gates"]["gates"]:
        print(f"  {g['level']:12s} {g['check']}: {g['detail'][:110]}")
    if r["validation_errors"]:
        print("  validation errors:", r["validation_errors"])
    return 1 if r["validation_errors"] else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SpecError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
