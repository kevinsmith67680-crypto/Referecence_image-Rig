"""Phase 3.8.1 evidence: before/after renders of the visual defect fixes.

``render_cases`` renders the requested cases and records, per render, the hand assets shown, their approval state,
the corrective artwork drawn, the constraint violations and the unresolved artwork. It uses only engine APIs that
already existed at Phase 3.8 (commit 8d31693), so the same function renders the "before" set from that commit
(run with its sources and package) and the "after" set from the current tree. ``compose`` builds the contact
sheets from the two sets. Nothing here approves anything: review renders preview pending drawings and are labelled.

Run as a module (``autorig visual-fix-evidence``) or as a script by path (``python visualfix_evidence.py render
<package> <out>``) with ``PYTHONPATH`` pointing at another commit's sources.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

HIDE = {"motion", "garments", "ik_switch", "correctives", "artistic", "performance", "hand_selection",
        "reference_motion", "closeups", "legacy", "poses"}
ARMS_DOWN = {"abduction": 10.0, "flexion": 5.0, "wrist": 0.0}
CASES: list[tuple[str, str, dict[str, Any]]] = [
    ("01_tpose", "T-pose", {"kind": "identity"}),
    ("02_relaxed_standing", "relaxed standing", {"kind": "preset", "name": "relaxed_standing"}),
    ("03_right_pointing", "right pointing", {"kind": "preset", "name": "pointing_speaking"}),
    ("04_left_pointing", "left pointing", {"kind": "arms", "arms": {"l": {"abduction": 95.0, "flexion": 8.0,
                                                                          "wrist": 0.0}, "r": ARMS_DOWN},
                                           "switches": {"hand_l": "pointing"}}),
    ("05_both_arms_lowered", "both arms lowered", {"kind": "preset", "name": "hands_at_sides"}),
    ("06_both_arms_raised", "both arms raised", {"kind": "preset", "name": "both_hands_raised"}),
    ("07_subtle_shrug", "subtle shrug (acting t=5.6 s)", {"kind": "acting", "t": 5.6}),
    ("08_weight_shift", "weight shift (acting t=2.33 s)", {"kind": "acting", "t": 2.33}),
    ("09_elbow_bend", "elbow bend (right, 110 deg)", {"kind": "arms", "arms": {"r": {"abduction": 35.0,
                                                                                    "flexion": 110.0, "wrist": 0.0},
                                                                              "l": ARMS_DOWN}}),
]
SWEEP = (0, 30, 60, 90, 120)
ACTING_TIMES = [round(0.6 * k, 2) for k in range(12)]


def _engine(pkg: Path):
    from autorig.garmentrig.correctives import CorrectiveArtworkStore, CorrectiveRuntime
    from autorig.motion.pipeline import engine_for_package

    eng, vis = engine_for_package(pkg)
    eng.correctives = CorrectiveRuntime(eng, CorrectiveArtworkStore(pkg))
    return eng, vis


def _alternatives(eng, pkg: Path, action: str, review: bool) -> tuple[dict[str, str] | None, list[dict[str, Any]]]:
    try:
        from autorig.artistic.alternatives import AlternativeArtworkController, AlternativeArtworkStore
    except ImportError:
        return None, []
    store = AlternativeArtworkStore(pkg)
    if not store.index.get("items"):
        return None, []
    ctl = AlternativeArtworkController(eng, store)
    chosen, rep = ctl.select(action, "FRONT", {}, review)
    return (chosen or None), [r for r in rep if r["selected"]]


def _sw(spec: dict[str, str] | None) -> dict[str, Any] | None:
    if not spec:
        return None
    return {g: {"user": v, "auto": False, "keys": [], "offsets": {}} for g, v in spec.items()}


def _record(eng, r: dict[str, Any], review: bool, alts: list[dict[str, Any]]) -> dict[str, Any]:
    solve = r.get("solve") or {}
    hands = {}
    for s in ("r", "l"):
        h = (r.get("hands") or {}).get(s) or {}
        g = (r.get("switches") or {}).get(f"hand_{s}") or {}
        dec = ((r.get("hand_selection") or {}).get("decisions") or {}).get(s) or {}
        v = eng.hand_lib.hands[s].get(h.get("pose")) if hasattr(eng, "hand_lib") else None
        hm = ((v.meta or {}).get("hand_meta") if v is not None else None) or {}
        issues = []
        if g and g.get("active") != h.get("pose"):
            issues.append(f"switch resolved to {g.get('active')!r}, render shows {h.get('pose')!r}")
        if g and g.get("requested") != g.get("active") and not g.get("note"):
            issues.append("silent substitution")
        if h.get("pending_review") and not review:
            issues.append("pending drawing shown outside a review preview")
        hands[s] = {"pose": h.get("pose"), "cid": h.get("cid"), "source": h.get("source"),
                    "approval": "PENDING_REVIEW" if h.get("pending_review") else (
                        "ORIGINAL" if h.get("cid") == "original" else "APPROVED"),
                    "requested": g.get("requested"), "active": g.get("active"), "note": g.get("note"),
                    "handedness": hm.get("handedness"),
                    "palm": ((dec.get("ranking") or [{}])[0].get("palm") or {}).get("palm_class")
                    if dec.get("chosen") else None,
                    "needs": [f"{n['pose']}:{n['status']}" for n in dec.get("needs_artwork") or []],
                    "consistency_issues": issues}
    corr = [{"asset": c.get("asset_id"), "status": c.get("status")} for c in (r.get("correctives") or [])
            if isinstance(c, dict) and c.get("shown")]
    feas = r.get("feasibility") or {}
    anat = solve.get("anatomical") or {}
    return {"hands": hands, "correctives": corr, "alternatives": [a["approval_key"] for a in alts],
            "violations": solve.get("violations") or [],
            "knees": {k: anat.get(k) for k in ("knee_r_flexion", "knee_l_flexion")},
            "balance_error_px": solve.get("balance_error_px"),
            "feasibility": feas.get("status"),
            "missing_artwork": sorted({str(m.get("asset") if isinstance(m, dict) else m)
                                       for m in (feas.get("missing") or [])})}


def _save(img: np.ndarray, path: Path) -> None:
    bg = Image.new("RGBA", (img.shape[1], img.shape[0]), (255, 255, 255, 255))
    bg.alpha_composite(Image.fromarray(img))
    bg.convert("RGB").save(path)


def render_cases(pkg: str | Path, out: str | Path, progress=print) -> dict[str, Any]:
    """Render every case in review and production mode, the shoulder sweep and the acting frames."""
    from autorig.newchar_evidence import arm_fn, identity_render

    pkg, out = Path(pkg), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    eng, vis = _engine(pkg)
    rec: dict[str, Any] = {"cases": {}, "sweep": {}, "acting": {}}
    for cid, title, spec in CASES:
        for mode in ("review", "production"):
            review = mode == "review"
            if spec["kind"] == "identity":
                r = identity_render(eng, vis)
                r.update(pad=0, solve={"joints_posed": {b.name: list(map(float, b.head)) for b in eng.sk.bones}})
                info = {"hands": {}, "note": "identity pose: the source pixels"}
            elif spec["kind"] == "acting":
                r = _director(eng, vis).solve(spec["t"], render=True)
                info = None
            else:
                kw: dict[str, Any] = {"visible": vis, "allow_pending": review,
                                      "corrective_mode": "all" if review else "auto"}
                name = spec.get("name", "relaxed_standing")
                if spec["kind"] == "arms":
                    kw["intent_fn"] = arm_fn(spec["arms"], True, eng.sk)
                if spec.get("switches"):
                    kw["switches"] = _sw(spec["switches"])
                lv, alts = _alternatives(eng, pkg, name, review)
                if lv:
                    kw["layer_variants"] = lv
                r = eng.pose(name, **kw)
                info = None
            if info is None:
                lv_ = r.get("layer_variants") or {}
                info = _record(eng, r, review if spec["kind"] != "acting" else True,
                               [{"approval_key": v} for v in lv_.values()])
            _save(r["image"], out / f"{cid}__{mode}.png")
            joints = {k: [float(v[0]) + r.get("pad", 0), float(v[1]) + r.get("pad", 0)]
                      for k, v in (r.get("solve") or {}).get("joints_posed", {}).items()}
            info["joints_px"] = joints
            rec["cases"].setdefault(cid, {"title": title})[mode] = info
            if spec["kind"] in ("identity", "acting"):
                note = ("identity pose: the source pixels" if spec["kind"] == "identity" else
                        "acting frame: the director renders a REVIEW PREVIEW (pending drawings labelled); no "
                        "production render of the acting test exists")
                rec["cases"][cid]["production"] = {**info, "note": note}
                _save(r["image"], out / f"{cid}__production.png")
                break
        progress(f"case {cid} done")
    for s in ("r", "l"):
        for ang in SWEEP:
            other = "l" if s == "r" else "r"
            fn = arm_fn({s: {"abduction": float(ang), "flexion": 0.0, "wrist": 0.0}, other: ARMS_DOWN}, True, eng.sk)
            lv, _ = _alternatives(eng, pkg, "relaxed_standing", True)
            kw = {"layer_variants": lv} if lv else {}
            r = eng.pose("relaxed_standing", visible=vis, allow_pending=True, intent_fn=fn, corrective_mode="all", **kw)
            _save(r["image"], out / f"sweep_{s}_{ang:03d}.png")
            J = r["solve"]["joints_posed"]
            rec["sweep"][f"{s}_{ang}"] = {"pivot_px": [J[f"upper_arm_{s}"][0] + r["pad"], J[f"upper_arm_{s}"][1] + r["pad"]],
                                          "variants": list((r.get("layer_variants") or {}).values())}
    progress("sweep done")
    d = _director(eng, vis)
    for t in ACTING_TIMES:
        r = d.solve(t, render=True)
        _save(r["image"], out / f"acting_{int(round(t * 100)):04d}.png")
        a = _record(eng, r, True, [])
        a["joints_px"] = {k: [float(v[0]) + r.get("pad", 0), float(v[1]) + r.get("pad", 0)]
                          for k, v in (r.get("solve") or {}).get("joints_posed", {}).items()}
        rec["acting"][str(t)] = a
    knees = []
    for k in range(0, int(d.duration * 48) + 1):
        s_ = d.solve(k / 48.0)["solve"]
        knees.append({"t": round(k / 48.0, 4), "knee_l": s_["anatomical"]["knee_l_flexion"],
                      "knee_r": s_["anatomical"]["knee_r_flexion"], "violations": s_.get("violations") or [],
                      "balance_error_px": s_.get("balance_error_px")})
    rec["acting_knees"] = knees
    progress("acting done")
    (out / "records.json").write_text(json.dumps(rec, indent=1, default=float))
    return rec


_DIRECTORS: dict[int, Any] = {}


def _director(eng, vis):
    from autorig.performance import scenarios
    from autorig.performance.director import PerformanceDirector

    if id(eng) not in _DIRECTORS:
        _DIRECTORS[id(eng)] = PerformanceDirector(eng, vis, scenarios.acting_upgrade())
    return _DIRECTORS[id(eng)]


# -- composition ------------------------------------------------------------------------------------------------
def _crop(path: Path, cx: float, cy: float, w: int, h: int, zoom: float) -> Image.Image:
    im = Image.open(path).convert("RGB")
    box = (int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2))
    return im.crop(box).resize((int(w * zoom), int(h * zoom)), Image.LANCZOS)


def _label(im: Image.Image, lines: list[str], colour=(0, 0, 0), width: int | None = None) -> Image.Image:
    w = width or im.width
    hh = 14 * len(lines) + 6
    out = Image.new("RGB", (max(w, im.width), im.height + hh), "white")
    d = ImageDraw.Draw(out)
    for i, ln in enumerate(lines):
        d.text((4, 3 + 14 * i), ln, fill=(200, 0, 0) if ("PENDING" in ln or "REVIEW" in ln) else colour)
    out.paste(im, (0, hh))
    return out


def _row(ims: list[Image.Image], gap: int = 8) -> Image.Image:
    w = sum(i.width for i in ims) + gap * (len(ims) - 1)
    h = max(i.height for i in ims)
    out = Image.new("RGB", (w, h), "white")
    x = 0
    for i in ims:
        out.paste(i, (x, 0))
        x += i.width + gap
    return out


def _col(ims: list[Image.Image], gap: int = 10, title: str | None = None) -> Image.Image:
    w = max(i.width for i in ims)
    h = sum(i.height for i in ims) + gap * (len(ims) - 1) + (26 if title else 0)
    out = Image.new("RGB", (w, h), (245, 245, 245))
    y = 0
    if title:
        ImageDraw.Draw(out).text((6, 6), title, fill=(0, 0, 0))
        y = 26
    for i in ims:
        out.paste(i, (0, y))
        y += i.height + gap
    return out


def _hand_line(rec: dict[str, Any], s: str) -> str:
    h = (rec.get("hands") or {}).get(s) or {}
    if not h:
        return f"{s}: -"
    tag = " PENDING REVIEW" if h.get("approval") == "PENDING_REVIEW" else ""
    cid = str(h.get("cid") or "").split("/")[-1]
    extra = f" | {h['note'][:60]}" if h.get("note") and h.get("requested") != h.get("active") else ""
    if h.get("source") == "default" and h.get("needs"):
        extra += " | fallback, " + ", ".join(h["needs"][:2])
    return f"{s}: {h.get('pose')} ({cid}, {h.get('source')}){tag}{extra}"


def compose(before: str | Path, after: str | Path, out: str | Path) -> dict[str, Any]:
    before, after, out = Path(before), Path(after), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    B = json.loads((before / "records.json").read_text())
    A = json.loads((after / "records.json").read_text())
    made = []

    # 1. hands: relaxed, right pointing, left pointing, arms lowered; both hands; before/after x review/production
    rows = []
    for cid in ("02_relaxed_standing", "03_right_pointing", "04_left_pointing", "05_both_arms_lowered"):
        tiles = []
        for tag, d, R in (("BEFORE (3.8)", before, B), ("AFTER (3.8.1)", after, A)):
            for mode in ("review", "production"):
                rec = R["cases"][cid][mode]
                J = rec.get("joints_px") or {}
                crops = [_crop(d / f"{cid}__{mode}.png", J[f"hand_{s}"][0], J[f"hand_{s}"][1] + 40, 190, 230, 1.2)
                         for s in ("r", "l") if f"hand_{s}" in J]
                tiles.append(_label(_row(crops), [f"{tag} {mode.upper()}{' PREVIEW' if mode == 'review' else ''}",
                                                  _hand_line(rec, "r"), _hand_line(rec, "l")], width=470))
        rows.append(_col([_row(tiles)], title=A["cases"][cid]["title"]))
    p = out / "hands_before_after.png"
    _col(rows, title="Hands: requested vs rendered, both hands. Review previews show pending drawings (labelled); "
                     "production shows approved drawings and the original hand only").save(p)
    made.append(p.name)

    # 2. shoulder sweep: both arms 0..120 deg from hanging, before/after
    rows = []
    for s in ("r", "l"):
        for tag, d, R in (("BEFORE", before, B), ("AFTER (review: pending shoulder candidate)", after, A)):
            tiles = []
            for ang in SWEEP:
                px, py = R["sweep"][f"{s}_{ang}"]["pivot_px"]
                tiles.append(_label(_crop(d / f"sweep_{s}_{ang:03d}.png", px, py + 20, 240, 240, 1.0),
                                    [f"{s} arm {ang} deg"]))
            rows.append(_label(_row(tiles), [f"{tag}"]))
    p = out / "shoulder_sweep_before_after.png"
    _col(rows, title="Shoulder transitions: each arm at 0, 30, 60, 90, 120 deg from hanging (abduction), the other arm "
                     "down. After: weights continuous across the torso/arm cut, armpit gusset backs the seam, pending "
                     "shoulder-rounding candidate previewed").save(p)
    made.append(p.name)

    # 3. armpit close-up at 120 deg, both arms
    tiles = []
    for s in ("r", "l"):
        for tag, d, R in (("BEFORE", before, B), ("AFTER", after, A)):
            px, py = R["sweep"][f"{s}_120"]["pivot_px"]
            tiles.append(_label(_crop(d / f"sweep_{s}_120.png", px + (30 if s == "r" else -30), py + 70, 150, 150,
                                      2.0), [f"{tag} {s} arm 120 deg, armpit (gusset PENDING REVIEW)"]))
    p = out / "armpit_before_after.png"
    _col([_row(tiles)], title="Armpit at 120 deg: the dashed light line along the sleeve underside (unbacked "
                              "anti-aliased edge) before, backed by the gusset after").save(p)
    made.append(p.name)

    # 4. knees: acting frames + knee readings
    tiles = []
    for t in ("0.6", "1.2", "2.4"):
        for tag, d, R in (("BEFORE", before, B), ("AFTER", after, A)):
            rec = R["acting"][t]
            J = rec.get("joints_px") or {}
            k = rec["knees"]
            img = d / f"acting_{int(round(float(t) * 100)):04d}.png"
            cx = (J["shin_l"][0] + J["shin_r"][0]) / 2 if "shin_l" in J else Image.open(img).width / 2
            cy = J["shin_l"][1] if "shin_l" in J else Image.open(img).height * 0.7
            v = [x for x in rec["violations"] if "knee" in x]
            tiles.append(_label(_crop(img, cx, cy, 420, 560, 0.8),
                                [f"{tag} t={t}s knee L {k['knee_l_flexion']:.1f} R {k['knee_r_flexion']:.1f}",
                                 ("VIOLATION " + v[0][:40]) if v else "within limits"]))
    kb = [x for x in B["acting_knees"] if any("knee" in v for v in x["violations"])]
    ka = [x for x in A["acting_knees"] if any("knee" in v for v in x["violations"])]
    mb = max((x["knee_l"] for x in B["acting_knees"]), default=0)
    ma = max((x["knee_l"] for x in A["acting_knees"]), default=0)
    p = out / "knee_before_after.png"
    _col([_row(tiles)], title=f"Knee limits in the acting test (48 Hz): frames with a knee violation {len(kb)} -> "
                              f"{len(ka)}; max left knee {mb:.1f} -> {ma:.1f} deg (limit 10 deg standing)").save(p)
    made.append(p.name)

    # 5. full character: all cases before/after (review)
    tiles = []
    for cid, title, _ in CASES:
        pair = []
        for tag, d in (("BEFORE", before), ("AFTER", after)):
            im = Image.open(d / f"{cid}__review.png").convert("RGB")
            im.thumbnail((300, 420))
            pair.append(_label(im, [f"{tag}"]))
        tiles.append(_label(_row(pair), [title]))
    grid_rows = [_row(tiles[i:i + 3], gap=16) for i in range(0, len(tiles), 3)]
    p = out / "full_character_before_after.png"
    _col(grid_rows, title="Full character, review previews (pending drawings and correctives shown, labelled in "
                          "records.json); left of each pair Phase 3.8, right Phase 3.8.1").save(p)
    made.append(p.name)

    # 6. short acting test (after): contact sheet + GIF
    frames = [Image.open(after / f"acting_{int(round(t * 100)):04d}.png").convert("RGB") for t in ACTING_TIMES]
    th = []
    for t, f in zip(ACTING_TIMES, frames):
        g = f.copy()
        g.thumbnail((260, 380))
        th.append(_label(g, [f"t={t}s REVIEW PREVIEW"]))
    p = out / "acting_after_contact_sheet.png"
    _col([_row(th[i:i + 6], gap=6) for i in range(0, len(th), 6)],
         title="Short acting test after the fixes (review preview: pending hands labelled)").save(p)
    made.append(p.name)
    gif = [f.resize((f.width // 2, f.height // 2)) for f in frames]
    gif[0].save(out / "acting_after.gif", save_all=True, append_images=gif[1:], duration=400, loop=0)
    made.append("acting_after.gif")
    summary = {"made": made, "knee_violation_frames": [len(kb), len(ka)], "max_knee_l": [mb, ma]}
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":  # script mode: render with whatever ``autorig`` PYTHONPATH provides
    if len(sys.argv) >= 4 and sys.argv[1] == "render":
        render_cases(sys.argv[2], sys.argv[3])
    else:
        print("usage: python visualfix_evidence.py render <package> <out>")


def candidates_sheet(pkg: str | Path, hand_grid: str | Path, out: str | Path) -> dict[str, Any]:
    """Previews of the non-destructive artwork candidates (``autorig.visualfix``). The candidate operations are applied
    to a scratch copy of the package only; the package itself is never changed. Everything shown is a proposal for a
    person's review."""
    import shutil
    import tempfile

    from autorig import visualfix as V
    from autorig.editor.project import EditorProject

    pkg, out = Path(pkg), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    eng, vis = _engine(pkg)
    rep: dict[str, Any] = {}
    eye = V.eye_white_candidate(eng)
    wrist_scale = V.wrist_scale_candidates(eng)
    hem = V.hem_mask_candidates(eng)
    fex = V.forearm_extension_candidate(eng)
    rep["eye_white"] = {s: {k: v for k, v in d.items() if k != "rgba"} for s, d in eye.items()}
    rep["wrist_scale"] = [{k: v for k, v in o.items()} for o in wrist_scale]
    rep["hem"] = hem["sides"]
    rep["forearm_extension"] = fex["sides"]
    rep["skin"] = V.skin_candidates(hand_grid)
    work = Path(tempfile.mkdtemp(prefix="p381_cand_"))
    dst = work / "character_package"
    shutil.copytree(pkg, dst, ignore=lambda d, n: [x for x in n if Path(d).name == "natural_pose" and x in HIDE]
                    + [x for x in n if x == "editor"])
    p = EditorProject(dst)
    for op in hem["ops"] + [o["op"] for o in wrist_scale]:
        p.submit(op)
    eng2, vis2 = _engine(dst)
    lv2 = {}
    for base, rgba in fex["variants"].items():
        eng2.dfm.add_variant_layer(f"{base}__nogen", rgba)
        eng2.variants[f"{base}__nogen"] = rgba
        lv2[base] = f"{base}__nogen"
    tiles, rows = [], []
    marks = {}
    for preset, review in (("relaxed_standing", False), ("pointing_speaking", False), ("relaxed_standing", True)):
        a = eng.pose(preset, visible=vis, allow_pending=review)
        b = eng2.pose(preset, visible=vis2, allow_pending=review, layer_variants=None if review else lv2)
        marks[f"{preset}/{'review' if review else 'production'}"] = [len(V.detached_marks(a["image"])),
                                                                     len(V.detached_marks(b["image"]))]
        pair = []
        for tag, r in (("CURRENT", a), ("WITH CANDIDATES (scratch copy)", b)):
            J, pad = r["solve"]["joints_posed"], r["pad"]
            img = r["image"]
            crops = []
            for s in ("r", "l"):
                x, y = J[f"hand_{s}"][0] + pad, J[f"hand_{s}"][1] + pad
                c = img[int(y) - 60:int(y) + 140, int(x) - 90:int(x) + 90]
                bg = Image.new("RGBA", (c.shape[1], c.shape[0]), (255, 255, 255, 255))
                bg.alpha_composite(Image.fromarray(c))
                crops.append(bg.convert("RGB"))
            x0, y0 = J["foot_r"][0] + pad, J["foot_r"][1] + pad
            x1 = J["foot_l"][0] + pad
            c = img[int(y0) - 70:int(y0) + 90, int(min(x0, x1)) - 90:int(max(x0, x1)) + 90]
            bg = Image.new("RGBA", (c.shape[1], c.shape[0]), (255, 255, 255, 255))
            bg.alpha_composite(Image.fromarray(c))
            crops.append(bg.convert("RGB"))
            pair.append(_label(_row(crops), [f"{tag}: {preset} {'REVIEW PREVIEW' if review else 'production'}"]))
        rows.append(_row(pair, gap=20))
    rep["detached_marks_current_vs_candidates"] = marks
    # eye whites: the layer alone (the generated part is under the iris at rest)
    eyes = []
    for s in ("r", "l"):
        d = eye.get(s) or {}
        if d.get("status") != "candidate":
            continue
        lay = eng.layers[d["layer"]]
        ys, xs = np.nonzero(lay[..., 3] > 0)
        sl = (slice(ys.min() - 4, ys.max() + 5), slice(xs.min() - 4, xs.max() + 5))
        for tag, im in (("current (FLUX, dark)", lay), ("candidate (sclera flat fill)", d["rgba"])):
            c = im[sl]
            bg = Image.new("RGBA", (c.shape[1], c.shape[0]), (200, 220, 200, 255))
            bg.alpha_composite(Image.fromarray(c))
            g = bg.convert("RGB")
            eyes.append(_label(g.resize((g.width * 4, g.height * 4), Image.NEAREST), [f"eye {s} white: {tag}"]))
    rows.append(_label(_row(eyes), ["Eye-white layers (the generated part sits under the iris at rest; PENDING REVIEW)"]))
    skin = []
    sk_dir = Path(hand_grid)
    packs = sorted((pkg / "hand_store" / "packs").glob("*/v*/hands"))
    for side, pose in (("right", "relaxed"), ("left", "open_palm")):
        f0 = (packs[-1] / side / f"{pose}.png") if packs else Path("-")
        f1 = sk_dir / "candidates" / f"{side}_{pose}__skin_matched.png"
        name = f"{side}_{pose}"
        if f0.is_file() and f1.is_file():
            for tag, f in (("library", f0), ("skin-matched candidate", f1)):
                im = Image.open(f).convert("RGBA")
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                bg.alpha_composite(im)
                skin.append(_label(bg.convert("RGB"), [f"{name} {tag}"]))
    if skin:
        rows.append(_label(_row(skin), ["Hand skin tone (candidates on disk, never imported or approved)"]))
    pth = out / "artwork_candidates.png"
    _col(rows, title="Non-destructive artwork candidates, previewed on a scratch copy (hem marks -> foot layer, "
                     "generated forearm extension rejected under the original hand, wrist scale corrections, eye-white flat fill, skin tone). "
                     "None is applied to the package; all need a person's review").save(pth)
    (out / "artwork_candidates.json").write_text(json.dumps(rep, indent=1, default=float))
    shutil.rmtree(work, ignore_errors=True)
    return rep
