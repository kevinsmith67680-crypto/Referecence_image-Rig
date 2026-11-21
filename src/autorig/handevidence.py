"""Hand Pose Store evidence: every hand candidate attached at both wrists on the real character.

``autorig hand-evidence <package>`` writes ``<package>/hand_store/evidence/``:
* ``attachment_sheet.jpg``: each candidate (original, Phase 2 drawing, imported drawings) on the hanging arm and on
  an IK-raised arm, with a zoomed seam at the cuff and the measured numbers;
* ``ik_switch_sweep.gif``: the right arm moved by IK while the hand switches between candidates every frame;
* ``results.json``: anchor/placement data, seam measures (gap, drift, cuff fill, overhang, duplicate-cuff pixels),
  switching pops and size consistency.

Pending drawings appear only as labelled PREVIEWs (the Hand Library ``hand_layers`` preview); nothing is approved.
Moho is not run.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from .handstore import STORE_DIR, posed_seam
from .motion.ik import LAT
from .motion.pipeline import engine_for_package, save_animation
from .motion.switches import ALIGN, posed_hand_alignment
from .phase2.pose_eval import on_bg

BG = (238, 240, 244)
SEAM_LIMITS = {"sleeve_gap_px": ALIGN["sleeve_gap_px"], "posed_attachment_drift_px": ALIGN["posed_attachment_drift_px"],
               "own_white_px_near_wrist": 60, "cuff_fill_min": 0.6}


def _crop(r: dict[str, Any], side: str, half: int = 95, zoom: float = 1.0) -> Image.Image:
    x, y = r["solve"]["joints_posed"][f"hand_{side}"]
    pad = r["pad"]
    img = on_bg(r["image"], BG).convert("RGB")
    b = (int(x + pad - half), int(y + pad - half), int(x + pad + half), int(y + pad + half))
    c = img.crop(b)
    return c.resize((int(c.width * zoom), int(c.height * zoom)), Image.LANCZOS) if zoom != 1.0 else c


def _seam_crop(r: dict[str, Any], eng, side: str, seam: dict[str, Any], half: int = 34, zoom: float = 3.0) -> Image.Image:
    """Zoom on the cuff edge (where the hand meets the sleeve)."""
    pose = r["pose"]
    hb = eng.sk.bone(f"hand_{side}")
    Wm = eng.solver.K.world(eng.sk.idx[f"hand_{side}"], pose)
    W = Wm[:2, :2] @ np.asarray(hb.head, float) + Wm[:2, 2]
    u = Wm[:2, :2] @ ((np.asarray(hb.tail, float) - hb.head) / max(hb.length, 1e-6))
    t = seam.get("line_t_px") or 0.0
    cx, cy = W + u * float(t if seam.get("line_t_px") is not None else 0.0)
    pad = r["pad"]
    img = on_bg(r["image"], BG).convert("RGB")
    c = img.crop((int(cx + pad - half), int(cy + pad - half), int(cx + pad + half), int(cy + pad + half)))
    return c.resize((int(c.width * zoom), int(c.height * zoom)), Image.NEAREST)


def _measure(eng, r: dict[str, Any], side: str, layer: str, rest_att) -> dict[str, Any]:
    pa = posed_hand_alignment(eng, r, side, layer, rest_att)
    sm = posed_seam(r, eng, side, layer)
    return {"gap_px": pa.get("sleeve_gap_px"), "drift_px": pa.get("posed_attachment_drift_px"),
            "cuff_fill": sm.get("fill"), "overhang_px": sm.get("overhang_px"), "cuff_width_px": sm.get("cuff_width_px"),
            "own_white_px_near_wrist": sm.get("own_white_px_near_wrist")}


def hand_evidence(pkg, out=None, progress=print) -> dict[str, Any]:
    pkg = Path(pkg)
    out = Path(out) if out else pkg / STORE_DIR / "evidence"
    out.mkdir(parents=True, exist_ok=True)
    eng, vis = engine_for_package(pkg)
    lib = eng.hand_lib
    pad = 160
    sh = {s: np.asarray(eng.plan_and_solve("relaxed_standing")[2]["joints_posed"][f"upper_arm_{s}"], float)
          for s in ("r", "l")}
    L = {s: eng.profile.arm(s) for s in ("r", "l")}
    ik_up = {f"arm_{s}": {"enabled": True, "mode": "ik", "bend": None, "pole": None,
                          "target": [float(sh[s][0] + LAT[s] * 0.62 * L[s]), float(sh[s][1] - 0.45 * L[s])]}
             for s in ("r", "l")}
    poses = [p for p in lib.candidates["r"] if lib.candidates["r"][p] or lib.candidates["l"][p]]
    base = {}
    for key, ik in (("hang", None), ("ik", ik_up)):
        r = eng.pose("relaxed_standing", visible=vis, pad=pad, ik=ik, allow_pending=True,
                     hand_layers={s: f"hand_{s}" for s in ("r", "l")})
        base[key] = r
    rows, results = [], []
    for pose in poses:
        n = max(len(lib.candidates["r"][pose]), len(lib.candidates["l"][pose]))
        for i in range(n):
            pair = {s: (lib.candidates[s][pose][i] if i < len(lib.candidates[s][pose]) else None) for s in ("r", "l")}
            layers = {s: v.layer for s, v in pair.items() if v is not None}
            renders = {key: eng.pose("relaxed_standing", visible=vis, pad=pad, ik=ik, allow_pending=True, hand_layers=layers)
                       for key, ik in (("hang", None), ("ik", ik_up))}
            cells = []
            for s in ("r", "l"):
                v = pair[s]
                if v is None:
                    cells.append(Image.new("RGB", (190 * 2 + 204, 230), (250, 235, 235)))
                    continue
                rec = {"side": s, "pose": pose, "cid": v.cid, "label": v.label, "status": v.status, "layer": v.layer,
                       "category": v.category, "placement": v.placement, "measures": {}}
                rec["rest_alignment"] = {k: lib.alignment_v(v, eng.profile.midline_x).get(k)
                                         for k in ("attachment_error_px", "width_ratio", "side_ok", "issues")}
                imgs = []
                for key in ("hang", "ik"):
                    r = renders[key]
                    m = _measure(eng, r, s, v.layer, v.measure["attachment"])
                    jb, jv = base[key]["solve"]["joints_posed"], r["solve"]["joints_posed"]
                    m["skeleton_change_px"] = round(max(float(np.hypot(jv[b][0] - jb[b][0], jv[b][1] - jb[b][1])) for b in jv), 4)
                    al_ = r["render"]["alphas"]
                    cov = next(al_[c] for c in (f"forearm_{s}__hscuff", f"forearm_{s}__trim", f"forearm_{s}") if c in al_)
                    m["visible_area_vs_original"] = round(float((al_[v.layer] & ~cov).sum()) / max(1.0, float(
                        (base[key]["render"]["alphas"][f"hand_{s}"] & ~base[key]["render"]["alphas"][f"forearm_{s}"]).sum())), 3)
                    rec["measures"][key] = m
                    imgs.append(_crop(r, s))
                sm = posed_seam(renders["hang"], eng, s, v.layer)
                seam = _seam_crop(renders["hang"], eng, s, {"line_t_px": None})
                bad = [k for k in ("gap_px", "drift_px") if (rec["measures"]["hang"].get(k) or 0) >
                       SEAM_LIMITS["sleeve_gap_px" if k == "gap_px" else "posed_attachment_drift_px"]]
                if (rec["measures"]["hang"].get("own_white_px_near_wrist") or 0) > SEAM_LIMITS["own_white_px_near_wrist"]:
                    bad.append("duplicate cuff")
                rec["flags"] = bad
                results.append(rec)
                cell = Image.new("RGB", (imgs[0].width * 2 + seam.width + 14, max(imgs[0].height, seam.height) + 64), "white")
                x = 0
                for im in imgs + [seam]:
                    cell.paste(im, (x, 64))
                    x += im.width + 7
                d = ImageDraw.Draw(cell)
                mh, mi = rec["measures"]["hang"], rec["measures"]["ik"]
                tag = "PREVIEW (pending review)" if v.status == "pending_review" else v.status.upper()
                d.text((4, 2), f"{'R' if s == 'r' else 'L'} {pose}: {v.label} [{tag}]", fill=(0, 0, 0))
                d.text((4, 17), f"hang: gap {mh['gap_px']} drift {mh['drift_px']} fill {mh['cuff_fill']} "
                                f"overhang {mh['overhang_px']} cuff px {mh['own_white_px_near_wrist']}", fill=(40, 40, 40))
                d.text((4, 32), f"IK: gap {mi['gap_px']} drift {mi['drift_px']} | skeleton change {mh['skeleton_change_px']} px "
                                f"| area x{mh['visible_area_vs_original']}", fill=(40, 40, 40))
                d.text((4, 47), ("OK" if not bad else "FLAG: " + ", ".join(bad)) +
                       (f" | scale {v.placement['final']['scale']}" if v.placement else ""),
                       fill=(30, 120, 40) if not bad else (180, 30, 30))
                del sm
                cells.append(cell)
            w = max(c.width for c in cells)
            row = Image.new("RGB", (w * 2 + 12, max(c.height for c in cells)), (236, 238, 242))
            for j, c in enumerate(cells):
                row.paste(c, (j * (w + 12), 0))
            rows.append(row)
            progress(f"  {pose} #{i}")
    W = max(r.width for r in rows)
    sheet = Image.new("RGB", (W, sum(r.height for r in rows) + 10 * len(rows) + 46), (236, 238, 242))
    d = ImageDraw.Draw(sheet)
    d.rectangle((0, 0, W, 40), fill=(34, 40, 60))
    d.text((8, 6), "Hand Pose Store attachment sheet: right | left. Per side: hanging arm, IK-raised arm, seam zoom x3. "
                   "PREVIEW = pending review (not approved).", fill=(255, 255, 255))
    d.text((8, 22), f"Limits: gap <= {SEAM_LIMITS['sleeve_gap_px']} px, drift <= {SEAM_LIMITS['posed_attachment_drift_px']} px, "
                    f"own cuff pixels <= {SEAM_LIMITS['own_white_px_near_wrist']}", fill=(210, 220, 255))
    y = 46
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.height + 10
    sheet_path = out / "attachment_sheet.jpg"
    if sheet.width > 2000:
        sheet = sheet.resize((2000, int(sheet.height * 2000 / sheet.width)), Image.LANCZOS)
    sheet.save(sheet_path, quality=85, optimize=True)

    # IK sweep with a hand switch every frame: the wrist must not pop, the hand must stay attached
    progress("  IK sweep with switching")
    cycle = [v for v in (lib.candidates["r"].get(p, []) for p in poses) for v in v]
    frames, sweep = [], []
    N = 16
    for k in range(N):
        a = math.radians(-15 + 175 * k / (N - 1))
        tgt = sh["r"] + 0.8 * L["r"] * np.array([LAT["r"] * math.sin(a), math.cos(a)])
        v = cycle[k % len(cycle)]
        ik = {"arm_r": {"enabled": True, "mode": "ik", "target": [float(tgt[0]), float(tgt[1])], "bend": 1}}
        r = eng.pose("relaxed_standing", visible=vis, pad=pad, ik=ik, allow_pending=True, hand_layers={"r": v.layer})
        m = _measure(eng, r, "r", v.layer, v.measure["attachment"])
        sweep.append({"frame": k, "cid": v.cid, "pose": v.pose, **m})
        frames.append(r["image"])
    acc = np.zeros(frames[0].shape[:2], bool)
    for f in frames:
        acc |= f[..., 3] > 0
    ys, xs = np.nonzero(acc)
    b = (int(xs.min()) - 10, int(ys.min()) - 10, int(xs.max()) + 10, int(ys.max()) + 10)
    save_animation([f[b[1]:b[3], b[0]:b[2]] for f in frames], 400, out / "ik_switch_sweep.gif", scale=0.45, bg=BG)

    # cuff mask demonstration: the drawing pulled 20 px out of the sleeve, with and without its mask
    progress("  cuff mask demonstration")
    from .handstore import engine_input
    from .motion.pipeline import MotionEngine

    demo_uid = next((v.cid[6:] for v in lib.candidates["r"].get("relaxed", []) if v.cid.startswith("store:")), None)
    mask_demo = None
    if demo_uid:
        base_v = {k: v for k, v in eng.variants.items() if "__hs." not in k and "__hscuff" not in k}
        tiles, mask_demo = [], {}
        for label, st_ in (("placed (masked)", {}), ("pulled out 20 px, masked", {"placement": {demo_uid: {"dx": -20.0}}}),
                           ("pulled out 20 px, NOT masked", {"placement": {demo_uid: {"dx": -20.0}},
                                                             "mask": {demo_uid: {"enabled": False}}})):
            hs = engine_input(pkg, st_, eng.sk, eng.layers, base_v, eng.approvals, eng.source)
            e2 = MotionEngine(eng.sk, eng.layers, eng.generated, eng.source, base_v, eng.approvals, hand_store=hs)
            lid = next(c["layer"] for c in hs["candidates"] if c["uid"] == demo_uid)
            r = e2.pose("relaxed_standing", visible=vis, pad=pad, allow_pending=True, hand_layers={"r": lid})
            sm = posed_seam(r, e2, "r", lid)
            mask_demo[label] = {"own_white_px": sm.get("own_white_px"), "own_cloth_px": sm.get("own_cloth_px")}
            t = _crop(r, "r", 80, 2.0)
            cell = Image.new("RGB", (t.width, t.height + 34), "white")
            ImageDraw.Draw(cell).text((4, 3), f"{label}", fill=(0, 0, 0))
            ImageDraw.Draw(cell).text((4, 18), f"own white (cuff) px: {sm.get('own_white_px')}", fill=(60, 60, 60))
            cell.paste(t, (0, 34))
            tiles.append(cell)
        demo = Image.new("RGB", (sum(t.width for t in tiles) + 16, tiles[0].height), (236, 238, 242))
        x = 0
        for t in tiles:
            demo.paste(t, (x, 0))
            x += t.width + 8
        demo.save(out / "cuff_mask_demo.jpg", quality=88)

    store_scales = {s: sorted({round(v.placement["final"]["scale"] / max(1e-9, (v.placement.get("correction") or {}).get("scale", 1.0)), 5)
                               for vs in lib.candidates[s].values() for v in vs if v.placement}) for s in ("r", "l")}
    res = {"package": pkg.name, "limits": SEAM_LIMITS, "variants": results, "ik_sweep": sweep,
           "ik_sweep_max": {"gap_px": max(x["gap_px"] or 0 for x in sweep), "drift_px": max(x["drift_px"] or 0 for x in sweep)},
           "size_consistency": {"auto_scale_per_side": store_scales,
                                "rule": "one automatic scale per side for every stored drawing (user corrections are listed per drawing)"},
           "files": {"sheet": sheet_path.name, "sweep": "ik_switch_sweep.gif", "mask_demo": "cuff_mask_demo.jpg"},
           "cuff_mask_demo": mask_demo,
           "note": "preview renders; pending drawings are not approved; Moho not run"}
    (out / "results.json").write_text(json.dumps(res, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)),
                                      encoding="utf-8")
    return res
