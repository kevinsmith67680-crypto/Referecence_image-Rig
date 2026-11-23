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
                         overwrite=args.overwrite, min_score=args.min_score,
                         complete_outline_px=args.complete_outline)
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
                       help="cumulative spending ceiling (default: AUTORIG_REPLICATE_BUDGET_USD or $5.00)")

    p = sub.add_parser("analyze", help="Phase 1: analyse a reference image into a character project")
    p.add_argument("image")
    p.add_argument("--out", required=True)
    p.add_argument("--provider", choices=["replicate", "fixture"], default="replicate")
    p.add_argument("--version", help="override the pinned vufinder/sam3 version (must be in the snapshot)")
    p.add_argument("--character-id", default="character")
    p.add_argument("--manual-landmarks", help="JSON {\"joints\": {name: [x, y]}} overriding estimated joints")
    p.add_argument("--min-score", type=float, default=0.5)
    p.add_argument("--overwrite", action="store_true", help="replace an existing AutoRig project at --out")
    p.add_argument("--complete-outline", type=int, default=0, metavar="PX",
                   help="opt-in: give dark silhouette outline clipped by the masks (up to PX px deep) back to the "
                        "nearest layer (opaque images on a uniform paper only; recorded in the pipeline steps)")
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

    p = sub.add_parser("motion", help="Phase 3.1: natural-motion evidence (presets, gait cycles, close-ups)")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("--out", help="output directory (default <package>/natural_pose/motion)")
    p.set_defaults(func=cmd_motion)

    p = sub.add_parser("motion-bench", help="Phase 3.1: measure solve and render times on this machine")
    p.add_argument("package")
    p.add_argument("--frames", type=int, default=120)
    p.add_argument("--renders", type=int, default=5)
    p.set_defaults(func=cmd_motion_bench)

    p = sub.add_parser("ik-demo", help="Phase 3.2: IK, foot contact and hand-switch evidence on a package")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("--out", help="output directory (default <package>/natural_pose/ik_switch)")
    p.set_defaults(func=cmd_ik_demo)

    p = sub.add_parser("rig-controls", help="Phase 3.2: write and validate the rig controls spec of a package")
    p.add_argument("package", help="Phase 2 character package (with editor/ edits, if any)")
    p.add_argument("--out", help="output JSON (default: print a summary only)")
    p.set_defaults(func=cmd_rig_controls)

    p = sub.add_parser("hand-import", help="Import a hand pose pack (zip) into a package's Hand Pose Store")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("pack", help="hand pack zip (manifest.json + transparent PNG sprites)")
    p.set_defaults(func=cmd_hand_import)

    p = sub.add_parser("hand-evidence", help="Hand Pose Store evidence: attachment sheet, seams, measurements")
    p.add_argument("package", help="Phase 2 character package with an imported hand store")
    p.add_argument("--out", help="output directory (default <package>/hand_store/evidence)")
    p.set_defaults(func=cmd_hand_evidence)

    p = sub.add_parser("hand-sheet", help="Extract a sheet of sleeve-free hand drawings into a validated hand pack")
    p.add_argument("sheet", help="sheet image (hands in a grid on a white, or near-black, background)")
    p.add_argument("--inspection", required=True, help="inspection record (gesture, wrist side, view per cell)")
    p.add_argument("--out", required=True, help="output directory (sprites, pack zip, extraction report, QA sheet)")
    p.add_argument("--library-id", required=True, help="library id of the pack")
    p.add_argument("--supersedes", nargs="*", default=[], help="library ids this pack replaces (archived on import)")
    p.add_argument("--zip-name", help="pack file name (default <library-id>.zip)")
    p.set_defaults(func=cmd_hand_sheet)

    p = sub.add_parser("hand-library", help="List hand libraries, or archive/restore one (editor project operation)")
    p.add_argument("package", help="Phase 2 character package with a hand store")
    p.add_argument("--archive", metavar="LIBRARY", help="archive a library (its drawings stop being candidates)")
    p.add_argument("--restore", metavar="LIBRARY", help="make an archived library active again")
    p.add_argument("--default", metavar="LIBRARY", help="drop the user's choice (superseded libraries are archived)")
    p.set_defaults(func=cmd_hand_library)

    p = sub.add_parser("hand-replace-evidence", help="Sleeve-free hand replacement evidence (renders, sheets, metrics)")
    p.add_argument("package", help="Phase 2 character package with the sleeve-free hand library imported")
    p.add_argument("--out", help="output directory (default <package>/hand_store/evidence_v2)")
    p.set_defaults(func=cmd_hand_replace_evidence)

    p = sub.add_parser("hand-audit", help="Phase 3.7: HandAssetMetadata inventory (measured + inspected) and sheet")
    p.add_argument("package", help="Phase 2 character package with hand drawings")
    p.add_argument("--inspection", help="semantic inspection record (e.g. references/schoolboy/hand_pack_v2/"
                                        "semantic_inspection.json); without it only measured fields are filled")
    p.set_defaults(func=cmd_hand_audit)

    p = sub.add_parser("hand-selection-evidence",
                       help="Phase 3.7: shrug diagnosis, 10 hand regression cases, close-ups, invalid-metadata tests")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("--quick", action="store_true", help="fewer frames (smoke run)")
    p.set_defaults(func=cmd_hand_selection_evidence)

    p = sub.add_parser("reference-models", help="Phase 3.7: check (or --download) the local pose-estimation models")
    p.add_argument("--download", action="store_true", help="download missing models (SHA-256 checked)")
    p.add_argument("--cache", help="model directory (default .autorig_cache/models)")
    p.set_defaults(func=cmd_reference_models)

    p = sub.add_parser("reference-import", help="Phase 3.7: import a reference (video/GIF/images/JSON/BVH) into the "
                                                "motion reference library")
    p.add_argument("media", help="MP4/MOV/GIF, an image directory or glob, AutoRig track JSON or BVH")
    p.add_argument("--id", required=True, help="reference id (lowercase, digits, _ -)")
    p.add_argument("--permission", required=True, help="usage permission statement (who owns it, why it may be used)")
    p.add_argument("--source", required=True, help="where the reference came from")
    p.add_argument("--gesture", help="gesture tag (pointing, shrug, walking ...)")
    p.add_argument("--category", default="gesture", help="gesture | locomotion | other")
    p.add_argument("--hand", action="append", default=[], metavar="SIDE=ACTION", help="hand action, e.g. r=pointing")
    p.add_argument("--fps", type=float, help="sampling fps for video (<= source, max 30)")
    p.add_argument("--library", help="library root (default motion_references/ or AUTORIG_MOTION_LIBRARY)")
    p.set_defaults(func=cmd_reference_import)

    p = sub.add_parser("reference-evidence",
                       help="Phase 3.7: synthetic references retargeted, MediaPipe round trip, critic and revision loop")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("--quick", action="store_true")
    p.set_defaults(func=cmd_reference_evidence)

    p = sub.add_parser("phase37-bench", help="Phase 3.7: timings on this machine + Moho-oriented hand/gesture "
                                             "metadata (proposed_unverified)")
    p.add_argument("package", help="Phase 2 character package")
    p.set_defaults(func=cmd_phase37_bench)

    p = sub.add_parser("newchar-evidence", help="Phase 3.8: new-character end-to-end evidence (numbered images, "
                                                "results.json)")
    p.add_argument("package", help="Phase 2 character package of the new character")
    p.add_argument("--project", required=True, help="its Phase 1 character project")
    p.add_argument("--character", required=True, help="the character reference image as imported")
    p.add_argument("--hand-sheet", required=True, help="the hand sheet image as imported")
    p.add_argument("--hand-grid", required=True, help="the hand-sheet output folder (sprites, extraction report)")
    p.add_argument("--out", default="docs/phase3_8/new_character_evidence")
    p.add_argument("--sections", nargs="*", help="subset of sections (default: all)")
    p.set_defaults(func=cmd_newchar_evidence)

    p = sub.add_parser("visual-fix-evidence", help="Phase 3.8.1: render the cases, compose before/after sheets and "
                                                   "the artwork-candidate previews")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("--before", help="a render folder made by visualfix_evidence.py render with an earlier commit's "
                                    "sources and package (omit to render and keep only the current set)")
    p.add_argument("--renders", default=None, help="where to write the current render set (default: <out>/renders/after)")
    p.add_argument("--hand-grid", default=None, help="hand-sheet output folder (skin-matched candidates)")
    p.add_argument("--out", default="docs/phase3_8_1")
    p.set_defaults(func=cmd_visual_fix_evidence)

    p = sub.add_parser("native-poc", help="Phase 4.0: build the minimal native Moho proof package (real artwork, "
                                          "shoulder + arm bones, build and verify-after-reopen scripts)")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("--out", required=True, help="output folder for the proof package")
    p.add_argument("--side", choices=("l", "r"), default="l", help="which arm (character's left = image right)")
    p.add_argument("--canvas", type=_canvas_arg, default=(1920, 1080),
                   help="Moho document size WxH (default 1920x1080), or 'source' for the package canvas")
    p.add_argument("--asset-root", default=None,
                   help="absolute POSIX path of --out on the Mac (bakes the paths into the scripts)")
    p.add_argument("--character-id", default=None, help="id for the rig spec (default: the package's parent folder)")
    p.set_defaults(func=cmd_native_poc)

    p = sub.add_parser("native-verify", help="Phase 4.0: check the native Moho run's evidence against HARD GATE 4.0")
    p.add_argument("poc", help="the proof package folder made by native-poc")
    p.add_argument("--evidence", help="evidence folder written by tools/macos/phase4_native_test.sh")
    p.add_argument("--build-report"); p.add_argument("--verify-report"); p.add_argument("--saved")
    p.add_argument("--render-rest", help="Moho render of frame 1")
    p.add_argument("--render-moved", help="Moho render of frame 12")
    p.add_argument("--render-reopened", help="Moho render of frame 18 after the verify script and save")
    p.add_argument("--out", help="gate JSON (default <evidence>/gate_4_0.json)")
    p.set_defaults(func=cmd_native_verify)

    p = sub.add_parser("native-fullbody", help="Phase 4.1: build the full-body native rig package (semantic skeleton, "
                                               "all layers unscaled, bindings, test poses, Lua scripts)")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("--out", required=True, help="output folder")
    p.add_argument("--canvas", type=_canvas_arg, default=None,
                   help="Moho document size WxH (default: sized automatically to hold every test pose)")
    p.add_argument("--asset-root", default=None, help="absolute POSIX path of --out on the Mac")
    p.add_argument("--character-id", default=None)
    p.add_argument("--config", default=None, help="JSON with per-character native rig settings (joint ranges ...)")
    p.add_argument("--include-pending-extensions", action="store_true",
                   help="review build: add the pending generated pixels beneath their originals (labelled pending)")
    p.set_defaults(func=cmd_native_fullbody)

    p = sub.add_parser("native-fullbody-verify", help="Phase 4.1: check native Mac evidence against gates 4.1-A..L")
    p.add_argument("package_out", help="the folder made by native-fullbody")
    p.add_argument("--evidence", help="evidence folder written by tools/macos/phase4_native_test.sh --phase 4.1")
    p.add_argument("--character-package", default=None, help="the character package (gate L re-derives the skeleton)")
    p.add_argument("--out", help="gate JSON (default <evidence>/gate_4_1.json)")
    p.set_defaults(func=cmd_native_fullbody_verify)

    p = sub.add_parser("deform-audit", help="Phase 4.2: joint deformation root-cause audit (SIMULATION of rigid "
                                            "native binding, variant experiments, pivot counterfactual)")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("--out", required=True, help="output folder (joint_audit.json + SIMULATION sheets)")
    p.add_argument("--variants", nargs="*", default=None, help="variants to measure (default: all six)")
    p.add_argument("--no-sheets", action="store_true")
    p.set_defaults(func=cmd_deform_audit)

    p = sub.add_parser("native-fills", help="Phase 4.2: bake native-geometry gap fills into the package's corrective "
                                            "store (append-only, pending review)")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("--regenerate", action="store_true", help="add new versions even when the assets exist")
    p.set_defaults(func=cmd_native_fills)

    p = sub.add_parser("native-hybrid", help="Phase 4.2: build the hybrid native rig package (split limb roots, layer "
                                             "order, correctives, Smart Bone plan, artwork requests, Lua scripts)")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("--out", required=True, help="output folder")
    p.add_argument("--review", action="store_true",
                   help="review build: add the pending clipped underlays (caps, shoe tops), labelled pending")
    p.add_argument("--include-pending-extensions", action="store_true",
                   help="with --review: also the pending Phase 2 reconstruction beneath the originals")
    p.add_argument("--canvas", type=_canvas_arg, default=None,
                   help="Moho document size WxH (default: sized automatically to hold every test pose)")
    p.add_argument("--asset-root", default=None, help="absolute POSIX path of --out on the Mac")
    p.add_argument("--audit", default=None, help="joint_audit.json from deform-audit (evidence for the requests)")
    p.set_defaults(func=cmd_native_hybrid)

    p = sub.add_parser("native-hybrid-verify", help="Phase 4.2: check native Mac evidence (gates 4.2-A..K)")
    p.add_argument("package_out", help="the folder made by native-hybrid")
    p.add_argument("--evidence", help="evidence folder written by tools/macos/phase4_native_test.sh --phase 4.2")
    p.add_argument("--character-package", default=None, help="the character package (joint sizes for gate E)")
    p.add_argument("--before", default=None, help="a Phase 4.1 native run folder (renders/) for the comparison")
    p.add_argument("--out", help="gate JSON (default <evidence>/gate_4_2.json)")
    p.set_defaults(func=cmd_native_hybrid_verify)

    p = sub.add_parser("garment-evidence", help="Phase 3.3: before/after garment renders, close-ups, sweeps, reports")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("--out", help="output directory (default <package>/natural_pose/garments)")
    p.set_defaults(func=cmd_garment_evidence)

    p = sub.add_parser("garment-weights", help="Phase 3.3: validate the garment weight meshes; explain a point")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("--explain", nargs=3, metavar=("LAYER", "X", "Y"), help="why a rest point follows its bones")
    p.add_argument("--export", help="write the weight meshes (npz) and the garment structure (json) to this prefix")
    p.set_defaults(func=cmd_garment_weights)

    p = sub.add_parser("corrective-evidence",
                       help="Phase 3.4: generate corrective artwork (pending review) and its evidence and reports")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("--out", help="output directory (default <package>/natural_pose/correctives)")
    p.add_argument("--regenerate", action="store_true", help="run the generators again (new asset versions)")
    p.add_argument("--quick", action="store_true", help="a few poses only (smoke run)")
    p.set_defaults(func=cmd_corrective_evidence)

    p = sub.add_parser("artistic-evidence",
                       help="Phase 3.5: artistic poses, candidates, silhouettes, folds, layer order, acting test")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("--out", help="output directory (default <package>/natural_pose/artistic)")
    p.add_argument("--quick", action="store_true", help="three poses, no acting test (smoke run)")
    p.set_defaults(func=cmd_artistic_evidence)

    p = sub.add_parser("performance-evidence",
                       help="Phase 3.6: upgraded acting test, classroom contacts, secondary motion, benchmark")
    p.add_argument("package", help="Phase 2 character package")
    p.add_argument("--out", help="output directory (default <package>/natural_pose/performance)")
    p.add_argument("--quick", action="store_true", help="every 6th frame only (smoke run)")
    p.set_defaults(func=cmd_performance_evidence)

    p = sub.add_parser("performance-bench", help="Phase 3.6: measure performance evaluation and render times here")
    p.add_argument("package", help="Phase 2 character package")
    p.set_defaults(func=cmd_performance_bench)

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


def cmd_motion(args: argparse.Namespace) -> int:
    from .motion.pipeline import motion_report

    r = motion_report(args.package, args.out)
    for k, c in r["cycles"].items():
        print(f"  cycle {k}: {c['numerical']['passed']}/{c['numerical']['total']} checks, "
              f"pelvis range {c['pelvis_vertical_range_px']} px, airborne samples {c['airborne_samples']}")
    return 0


def cmd_ik_demo(args: argparse.Namespace) -> int:
    from .motion.demo import ik_demo

    r = ik_demo(args.package, args.out)
    q = r["ik_sequence"]
    print(f"  IK sweep: {q['dense_samples']} samples, elbow flips {q['elbow_side_flips']}, max wrist error "
          f"{q['max_wrist_error_px']} px, max joint step {q['max_joint_step_px']} px")
    p = r["performance"]
    print(f"  perf: IK solve {p['two_bone_ik_solve_us']} us, skeleton {p['skeleton_solve_with_arm_ik_ms']} ms, "
          f"switches {p['switch_resolution_ms']} ms, full frame {p['full_frame_ms']} ms")
    return 0


def cmd_rig_controls(args: argparse.Namespace) -> int:
    import json

    from .editor.project import EditorProject
    from .motion.pipeline import engine_for_package
    from .motion.rigctl import build_rig_controls, validate

    eng, _ = engine_for_package(args.package)
    st = EditorProject(args.package).state()
    doc = build_rig_controls(eng, st.get("ik"), st.get("switches"), st.get("hand_selection"))
    errs = validate(doc)
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"rig controls {doc['schema_version']}: {len(doc['ik']['chains'])} IK chains, "
          f"{len(doc['switches']['groups'])} switch groups, {len(doc['corrections']['triggers'])} correction triggers, "
          f"{len(doc['missing_artwork'])} missing drawings; validation errors: {len(errs)}")
    for e in errs:
        print(f"  {e}")
    return 1 if errs else 0


def cmd_hand_import(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .handstore import HandStore, PackError

    try:
        r = HandStore(args.package).import_pack(Path(args.pack).read_bytes(), Path(args.pack).name)
    except PackError as exc:
        print("REJECTED:")
        for e in exc.errors:
            print(f"  - {e}")
        return 2
    print(f"{r['status']}: {r['library_id']} v{r['version']} ({r['assets']} drawings, all pending review)")
    for w in r.get("warnings", []):
        print(f"  warning: {w}")
    return 0


def cmd_hand_sheet(args: argparse.Namespace) -> int:
    from .handsheet import build_pack

    try:
        r = build_pack(args.sheet, args.inspection, args.out, args.library_id, args.supersedes, args.zip_name)
    except ValueError as exc:
        print(f"REJECTED: {exc}")
        return 2
    sm = r["summary"]
    print(f"{r['zip']}: {sm['assets']} drawings from {sm['drawings']} cells; wrist openings: {sm['open_cut']} open cuts, "
          f"{sm['closed_outline']} closed outlines; mirrored pairs {sm['mirrored_pairs']}, not mirrored: "
          f"{sm['not_mirrored_pairs'] or 'none'}; derived: {sm['derived_assets'] or 'none'}")
    print(f"  manual review: {', '.join(sm['manual_review']) or 'none'}")
    return 0


def cmd_hand_library(args: argparse.Namespace) -> int:
    from .editor.project import EditorProject, OpError
    from .handstore import HandStore, library_status

    proj = EditorProject(args.package)
    for flag, status in ((args.archive, "archived"), (args.restore, "active"), (args.default, None)):
        if flag:
            try:
                proj.submit({"type": "handstore.library", "library": flag, "status": status})
            except OpError as exc:
                print(f"REJECTED: {exc}")
                return 2
    st = proj.state().get("hand_store") or {}
    for lib, rec in library_status(HandStore(args.package), st).items():
        print(f"{lib}: {rec['status']} ({rec['reason']}); sleeve {rec['sleeve']}")
    return 0


def cmd_garment_evidence(args: argparse.Namespace) -> int:
    from .garmentrig.evidence import garment_evidence

    res = garment_evidence(args.package, args.out, progress=print)
    print(f"garment evidence: {len(res['tests'])} tests, mean pose {res['performance']['pose_wall_s_mean']} s; "
          "visual review required")
    return 0


def cmd_performance_evidence(args: argparse.Namespace) -> int:
    from .performance.evidence import performance_evidence

    rep = performance_evidence(args.package, args.out, quick=args.quick, progress=print)
    r = rep["results"]
    print(json.dumps({k: r[k]["motion"]["verdict"] for k in ("acting", "classroom")}, indent=1))
    print("Human artistic review is still required; pending drawings were previewed. Moho was not run.")
    return 0


def cmd_performance_bench(args: argparse.Namespace) -> int:
    from .garmentrig.correctives import CorrectiveArtworkStore, CorrectiveRuntime
    from .motion.pipeline import engine_for_package
    from .performance.evidence import benchmark

    eng, vis = engine_for_package(args.package)
    eng.correctives = CorrectiveRuntime(eng, CorrectiveArtworkStore(args.package))
    print(json.dumps(benchmark(eng, vis), indent=1, default=str))
    print("Measured on this machine only. Run it on the Apple M1 for M1 figures.")
    return 0


def cmd_artistic_evidence(args: argparse.Namespace) -> int:
    from .artistic.evidence import artistic_evidence

    rep = artistic_evidence(args.package, args.out, quick=args.quick, progress=print)
    poses = rep["results"]["poses"]
    print(json.dumps({k: {"action": v["action"], "class": v["feasibility"]["class"], "variant": v["recommended"]}
                      for k, v in poses.items()}, indent=1))
    print("Human artistic review is still required: approve poses in the editor's Artistic Review tab. Moho was not run.")
    return 0


def cmd_corrective_evidence(args: argparse.Namespace) -> int:
    from .garmentrig.corrective_evidence import corrective_evidence

    rep = corrective_evidence(args.package, args.out, regenerate=args.regenerate, quick=args.quick, progress=print)
    st = {k: v["status"] for k, v in rep["results"]["poses"].items()}
    print(json.dumps({"poses": st, "benchmark": rep["results"]["benchmark"]}, indent=1))
    print("All correctives are pending review: approve them in the editor (Correctives tab). Moho was not run.")
    return 0


def cmd_garment_weights(args: argparse.Namespace) -> int:
    import json

    import numpy as np

    from .motion.pipeline import engine_for_package

    eng, _ = engine_for_package(args.package)
    v = eng.weights.validate()
    print(f"weight meshes: {v['meshes']}, active vertices {v['vertices']}, issues {v['n_issues']}, "
          f"op errors {len(v['op_errors'])}")
    for i in v["issues"][:20]:
        print(f"  {i}")
    if args.explain:
        cid, x, y = args.explain
        print(json.dumps(eng.weights.explain(cid, (float(x), float(y))), indent=1))
    if args.export:
        np.savez_compressed(f"{args.export}_weights.npz", **eng.weights.export_arrays())
        with open(f"{args.export}_structure.json", "w", encoding="utf-8") as f:
            json.dump(eng.structure.to_json(), f, indent=1, default=str)
        print(f"wrote {args.export}_weights.npz and {args.export}_structure.json")
    return 0 if v["ok"] else 1


def cmd_newchar_evidence(args: argparse.Namespace) -> int:
    from .newchar_evidence import newchar_evidence

    r = newchar_evidence(args.package, args.project, {"character": args.character, "hand_sheet": args.hand_sheet},
                         args.hand_grid, args.out, args.sections, progress=lambda m: print(m, flush=True))
    print(json.dumps({k: v.get("images") for k, v in r.items() if isinstance(v, dict) and "images" in v}, indent=1))
    return 0


def cmd_visual_fix_evidence(args: argparse.Namespace) -> int:
    from .visualfix_evidence import candidates_sheet, compose, render_cases

    out = Path(args.out)
    renders = Path(args.renders) if args.renders else out / "renders" / "after"
    render_cases(args.package, renders, progress=lambda m: print(m, flush=True))
    res: dict = {"renders": str(renders)}
    if args.before:
        res["compose"] = compose(args.before, renders, out)
    if args.hand_grid:
        rep = candidates_sheet(args.package, args.hand_grid, out)
        res["candidates"] = {k: rep[k] for k in ("hem", "forearm_extension", "detached_marks_current_vs_candidates")}
    print(json.dumps(res, indent=1, default=str))
    return 0


def _canvas_arg(text: str):
    if text == "source":
        return None
    try:
        w, h = (int(v) for v in text.lower().split("x"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"canvas must be WxH or 'source', got {text!r}") from exc
    return (w, h)


def cmd_native_poc(args: argparse.Namespace) -> int:
    from .native.poc import NativePocError, build_poc

    try:
        man = build_poc(args.package, args.out, side=args.side, canvas=args.canvas, asset_root=args.asset_root,
                        character_id=args.character_id)
    except NativePocError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: man[k] for k in ("character_id", "side", "source_canvas", "moho_canvas", "placement",
                                         "split_fidelity", "scripts", "asset_root", "expected")}, indent=1))
    if not man["asset_root"]:
        print("note: reference build (no --asset-root): the Moho scripts refuse to run until regenerated on the Mac "
              "by tools/macos/phase4_native_test.sh", file=sys.stderr)
    return 0


def cmd_native_verify(args: argparse.Namespace) -> int:
    from .native.gate import evaluate_gate, format_gate

    renders = {"rest": args.render_rest, "moved": args.render_moved, "reopen_move": args.render_reopened}
    res = evaluate_gate(args.poc, args.evidence, build_report=args.build_report,
                        verify_report_path=args.verify_report, saved=args.saved,
                        renders={k: v for k, v in renders.items() if v})
    out = Path(args.out) if args.out else (Path(args.evidence) / "gate_4_0.json" if args.evidence else None)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=2, default=str) + "\n", encoding="utf-8")
    print(format_gate(res))
    if out:
        print(f"Gate result written: {out}")
    return 0 if res["passed"] else 1


def cmd_native_fullbody(args: argparse.Namespace) -> int:
    from .native.fullbody import NativeFullbodyError, build_fullbody

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8")) if args.config else None
    try:
        man = build_fullbody(args.package, args.out, canvas=args.canvas, asset_root=args.asset_root,
                             character_id=args.character_id,
                             include_pending_extensions=args.include_pending_extensions, config=cfg)
    except (NativeFullbodyError, ValueError) as exc:
        problems = getattr(exc, "problems", [str(exc)])
        for pr in problems:
            print(f"error: {pr}", file=sys.stderr)
        return 2
    print(json.dumps({k: man[k] for k in ("character_id", "source_canvas", "moho_canvas", "placement", "bones",
                                         "binding_counts", "scripts", "asset_root")} |
                     {"layers": {k: man["layers"][k] for k in ("count", "originals", "pending_extensions",
                                                              "all_originals_unchanged")}}, indent=1))
    print("note: this prepares the package; native behaviour is verified only by the Mac run "
          "(tools/macos/phase4_native_test.sh --phase 4.1)", file=sys.stderr)
    return 0


def cmd_native_fullbody_verify(args: argparse.Namespace) -> int:
    from .native.gate41 import evaluate_gate41, format_gate41

    res = evaluate_gate41(args.package_out, args.evidence, character_package=args.character_package)
    out = Path(args.out) if args.out else (Path(args.evidence) / "gate_4_1.json" if args.evidence else None)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=2, default=str) + "\n", encoding="utf-8")
    print(format_gate41(res))
    if out:
        print(f"Gate result written: {out}")
    return 0 if res["passed"] else 1


def cmd_deform_audit(args: argparse.Namespace) -> int:
    from .native.deform_audit import DEFAULT_VARIANTS, run_audit

    r = run_audit(args.package, variants=tuple(args.variants or DEFAULT_VARIANTS), out=args.out,
                  sheets=not args.no_sheets)
    print(json.dumps(r["summary"], indent=1, default=str))
    print("note: SIMULATION of rigid native binding; not Moho output", file=sys.stderr)
    return 0


def cmd_native_fills(args: argparse.Namespace) -> int:
    from .native.correctives42 import generate_native_fills

    r = generate_native_fills(args.package, regenerate=args.regenerate, progress=lambda m: print(m, flush=True))
    print(json.dumps(r, indent=1))
    print("note: the fills are pending; only a person approves them (autorig review / the editor)", file=sys.stderr)
    return 0


def cmd_native_hybrid(args: argparse.Namespace) -> int:
    from .native.hybrid_build import NativeHybridError, build_hybrid

    try:
        man = build_hybrid(args.package, args.out, review=args.review, extensions=args.include_pending_extensions,
                           canvas=args.canvas, asset_root=args.asset_root, audit=args.audit)
    except (NativeHybridError, ValueError) as exc:
        for pr in getattr(exc, "problems", [str(exc)]):
            print(f"error: {pr}", file=sys.stderr)
        return 2
    print(json.dumps({k: man[k] for k in ("character_id", "variant", "source_canvas", "moho_canvas", "placement",
                                         "bones", "scripts", "asset_root", "manual_switch_assets")} |
                     {"layers": {k: man["layers"][k] for k in ("count", "by_status", "partition_exact")}}, indent=1))
    print("note: this prepares the package; native behaviour is verified only by the Mac run "
          "(tools/macos/phase4_native_test.sh --phase 4.2)", file=sys.stderr)
    return 0


def cmd_native_hybrid_verify(args: argparse.Namespace) -> int:
    from .native.gate42 import VERDICT_PASS, comparison_sheets, evaluate_gate42, format_gate42

    res = evaluate_gate42(args.package_out, args.evidence, character_package=args.character_package,
                          before_dir=args.before)
    out = Path(args.out) if args.out else (Path(args.evidence) / "gate_4_2.json" if args.evidence else None)
    if args.evidence:
        res["comparisons"] = comparison_sheets(args.package_out, args.evidence, Path(args.evidence) / "comparisons",
                                               args.before)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=2, default=str) + "\n", encoding="utf-8")
    print(format_gate42(res))
    if out:
        print(f"Gate result written: {out}")
    return 0 if res["verdict"] == VERDICT_PASS else 1


def cmd_hand_audit(args: argparse.Namespace) -> int:
    from .handaudit import hand_audit

    r = hand_audit(args.package, args.inspection, progress=lambda m: print(m, flush=True))
    print(json.dumps(r["summary"], indent=1))
    return 0


def cmd_hand_selection_evidence(args: argparse.Namespace) -> int:
    from .handselect_evidence import hand_selection_evidence

    r = hand_selection_evidence(args.package, quick=args.quick, progress=lambda m: print(m, flush=True))
    print(json.dumps(r.get("summary", {}), indent=1, default=str))
    return 0


def cmd_reference_models(args: argparse.Namespace) -> int:
    from pathlib import Path as _P

    from .refmotion.pose import ensure_models, mediapipe_available

    st = ensure_models(cache=_P(args.cache) if args.cache else None, download=args.download)
    print(json.dumps({"mediapipe": mediapipe_available() or "ok", "models": st}, indent=1))
    return 0 if all(v["ok"] for v in st.values()) else 1


def cmd_reference_import(args: argparse.Namespace) -> int:
    from .refmotion.analyze import ReferenceMotionAnalyzer
    from .refmotion.importer import ReferenceMotionImporter
    from .refmotion.pose import MediaPipePoseProvider
    from .refmotion.store import MotionReferenceStore, default_root

    hands = dict(h.split("=", 1) for h in args.hand)
    meta = {"name": args.id, "gesture": args.gesture, "category": args.category, "hand_actions": hands or None,
            "provenance": {"kind": "user_supplied", "source": args.source, "usage_permission": args.permission}}
    media = ReferenceMotionImporter().load(args.media, meta, args.fps)
    tr = media.track if media.track is not None else MediaPipePoseProvider().estimate(
        media.frames, media.fps, progress=lambda m: print(m, flush=True))
    tr.meta = {**tr.meta, **meta}
    q = ReferenceMotionImporter.quality(tr)
    an = ReferenceMotionAnalyzer().analyze(tr)
    store = MotionReferenceStore(args.library or default_root())
    rec = store.add(args.id, tr, {**meta, "quality": q}, an)
    print(json.dumps({"stored": rec["id"], "v": rec["v"], "quality": q, "tags": rec["tags"]}, indent=1, default=str))
    return 0


def cmd_reference_evidence(args: argparse.Namespace) -> int:
    from .refmotion.evidence import reference_evidence

    r = reference_evidence(args.package, quick=args.quick, progress=lambda m: print(m, flush=True))
    print(json.dumps({k: (v.get("feasibility", {}).get("class") if isinstance(v, dict) else v)
                      for k, v in r["references"].items()}, indent=1))
    return 0


def cmd_phase37_bench(args: argparse.Namespace) -> int:
    from pathlib import Path as _P

    from .editor.project import EditorProject
    from .garmentrig.correctives import CorrectiveArtworkStore, CorrectiveRuntime
    from .motion.pipeline import engine_for_package
    from .refmotion.export import benchmark, phase37_metadata

    eng, vis = engine_for_package(args.package)
    eng.correctives = CorrectiveRuntime(eng, CorrectiveArtworkStore(args.package))
    out = _P(args.package) / "natural_pose" / "hand_selection"
    out.mkdir(parents=True, exist_ok=True)
    b = benchmark(eng, vis)
    (out / "benchmark.json").write_text(json.dumps(b, indent=1) + "\n", encoding="utf-8")
    m = phase37_metadata(eng, vis, EditorProject(args.package).state().get("switches"))
    (_P(args.package) / "analysis" / "phase37_moho_metadata.json").write_text(json.dumps(m, indent=1, default=float)
                                                                           + "\n", encoding="utf-8")
    print(json.dumps(b, indent=1))
    return 0


def cmd_hand_replace_evidence(args: argparse.Namespace) -> int:
    from .handreplace import replacement_evidence

    r = replacement_evidence(args.package, args.out)
    print(f"  {len(r['tests'])} test renders; gates: " + ", ".join(f"{g['id']} {g['status']}" for g in r["gates"]))
    return 0


def cmd_hand_evidence(args: argparse.Namespace) -> int:
    from .handevidence import hand_evidence

    r = hand_evidence(args.package, args.out)
    print(f"  {len(r['variants'])} variants measured; sheet {r['files']['sheet']}")
    return 0


def cmd_motion_bench(args: argparse.Namespace) -> int:
    import time

    from .motion.pipeline import engine_for_package

    t0 = time.perf_counter()
    eng, vis = engine_for_package(args.package)
    print(f"engine build: {time.perf_counter() - t0:.2f} s")
    solve = []
    for i in range(args.frames):
        t = time.perf_counter()
        eng.pose("running", (i / args.frames) % 1.0, visible=vis, skeleton_only=True)
        solve.append(time.perf_counter() - t)
    s = sorted(solve)
    print(f"skeleton solve ({args.frames} run phases): median {1000 * s[len(s) // 2]:.2f} ms, "
          f"p95 {1000 * s[int(0.95 * len(s))]:.2f} ms -> {1 / max(s[len(s) // 2], 1e-9):.0f} solves/s")
    ren = []
    for i in range(args.renders):
        t = time.perf_counter()
        eng.pose("running", i / max(args.renders, 1), visible=vis)
        ren.append(time.perf_counter() - t)
    r = sorted(ren)
    print(f"full artwork render ({args.renders} frames): median {r[len(r) // 2]:.2f} s, max {r[-1]:.2f} s")
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
