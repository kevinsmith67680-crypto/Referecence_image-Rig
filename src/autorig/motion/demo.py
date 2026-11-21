"""Phase 3.2 evidence on a real package: IK, foot contacts, hand switches, corrections, performance.

``autorig ik-demo <package>`` writes ``natural_pose/ik_switch/``: clean and diagnostic renders of the requested
demonstrations, close-ups (wrist, elbow, knees, sleeves), a hand-switch comparison sheet with attachment errors
measured in source pixels, an animated IK sequence with continuity metrics, and ``results.json``.

Everything here is a preview render of this repository's deformer. Moho is not run. Pending-review drawings appear
only in images labelled PREVIEW; nothing is approved here.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from ..phase2.pose_eval import on_bg
from ..phase2.skeleton import SIDES
from .ik import LAT, TwoBoneIKSolver
from .pipeline import engine_for_package, save_animation
from .switches import ALIGN, EXTRA_HAND_POSES, HAND_POSES, REQUIRED_HAND_POSES, offset_layer, posed_hand_alignment

CONTACT_RGB = {"PLANTED": (47, 158, 68), "LIFTING": (240, 140, 0), "SWINGING": (28, 126, 214),
               "LANDING": (174, 62, 201), "FREE": (134, 142, 150)}
BG = (238, 240, 244)


def _font_text(d: ImageDraw.ImageDraw, xy, text: str, fill=(0, 0, 0)) -> None:
    d.text(xy, text, fill=fill)


class Demo:
    def __init__(self, pkg: Path, progress=print):
        self.pkg = Path(pkg)
        self.progress = progress
        t0 = time.perf_counter()
        self.eng, self.vis = engine_for_package(self.pkg)
        self.build_s = time.perf_counter() - t0
        e = self.eng
        self.J0 = {b.name: b.head for b in e.sk.bones}
        self.L = {s: e.profile.arm(s) for s in SIDES}
        self.pad = 160
        self.timings: list[dict[str, float]] = []

    # -- helpers --------------------------------------------------------------------------------------
    def shoulder(self, s: str) -> np.ndarray:
        return np.asarray(self.eng.sk.bone(f"upper_arm_{s}").head, float)

    def arm_target(self, s: str, lat: float, down: float) -> list[float]:
        """World wrist target relative to the rest shoulder, in arm lengths (lat: + = outward, down: + = down)."""
        p = self.shoulder(s) + np.array([LAT[s] * lat * self.L[s], down * self.L[s]])
        return [round(float(p[0]), 2), round(float(p[1]), 2)]

    def render(self, name: str, phase=None, ik=None, switches=None, allow_pending=False, **kw) -> dict[str, Any]:
        r = self.eng.pose(name, phase, allow_pending=allow_pending, visible=self.vis, pad=self.pad, ik=ik,
                          switches=switches, **kw)
        self.timings.append(r["timing_ms"])
        return r

    def box(self, rs: list[dict[str, Any]], margin: int = 30) -> tuple[int, int, int, int]:
        acc = None
        for r in rs:
            a = r["image"][..., 3] > 0
            acc = a if acc is None else (acc | a)
        ys, xs = np.nonzero(acc)
        H, W = acc.shape
        return (max(0, int(xs.min()) - margin), max(0, int(ys.min()) - margin),
                min(W, int(xs.max()) + margin), min(H, int(ys.max()) + margin))

    def overlay(self, r: dict[str, Any], ik: dict[str, Any] | None) -> Image.Image:
        """Diagnostic overlay: skeleton, IK targets (red), poles (purple), reach circles, foot contacts."""
        img = on_bg(r["image"], BG).convert("RGB")
        d = ImageDraw.Draw(img)
        pad = r["pad"]
        P = lambda p: (float(p[0]) + pad, float(p[1]) + pad)
        s = r["solve"]
        J, T = s["joints_posed"], s["bone_tails_posed"]
        for b in self.eng.sk.bones:
            col = (255, 159, 67) if b.side == "r" else (72, 219, 251) if b.side == "l" else (254, 202, 87)
            d.line([P(J[b.name]), P(T[b.name])], fill=col, width=3)
            x, y = P(J[b.name])
            d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(28, 126, 214), outline=(255, 255, 255))
        for limb, c in (ik or {}).items():
            if limb == "pelvis_offset" or not c.get("enabled") or c.get("mode") != "ik" or c.get("target") is None:
                continue
            kind, side = limb.split("_")
            root = J[f"{'upper_arm' if kind == 'arm' else 'thigh'}_{side}"]
            mid = J[f"{'forearm' if kind == 'arm' else 'shin'}_{side}"]
            eff = T[f"{'forearm' if kind == 'arm' else 'shin'}_{side}"]
            ctl = self.eng.limb(limb)
            R = ctl.solver.max_reach
            x, y = P(root)
            d.ellipse((x - R, y - R, x + R, y + R), outline=(150, 150, 160), width=1)
            tx, ty = P(c["target"])
            d.line([(tx - 9, ty), (tx + 9, ty)], fill=(224, 49, 49), width=3)
            d.line([(tx, ty - 9), (tx, ty + 9)], fill=(224, 49, 49), width=3)
            d.line([P(eff), (tx, ty)], fill=(224, 49, 49), width=1)
            rep = (s["arms"] if kind == "arm" else s["legs"]).get(side, {})
            pole = c.get("pole") or rep.get("pole")
            if pole is not None:
                px_, py_ = P(pole)
                if abs(px_) < 1e4 and abs(py_) < 1e4 and 0 <= px_ < img.width and 0 <= py_ < img.height:
                    d.line([P(mid), (px_, py_)], fill=(112, 72, 232), width=1)
                    d.polygon([(px_, py_ - 7), (px_ + 7, py_), (px_, py_ + 7), (px_ - 7, py_)], fill=(112, 72, 232))
                else:
                    d.text((P(mid)[0] + 8, P(mid)[1]), "pole: far (bend side held)", fill=(112, 72, 232))
        for side, c in (s.get("contacts") or {}).items():
            x, y = P(c["ankle"])
            col = CONTACT_RGB.get(c["state"], (0, 0, 0))
            d.ellipse((x - 7, y + 20 - 7, x + 7, y + 20 + 7), fill=col, outline=(255, 255, 255))
            txt = c["state"] + (f" lock {c['lock_error_px']:.2f}px" if c["lock_error_px"] is not None else "")
            d.text((x - 40, y + 30), txt, fill=col)
        return img

    def tile(self, img: Image.Image, box, title: str, lines: list[str], colour=(34, 40, 60)) -> Image.Image:
        im = img.crop(box)
        head = 26 + 15 * len(lines)
        cell = Image.new("RGB", (im.width, im.height + head), "white")
        d = ImageDraw.Draw(cell)
        d.rectangle((0, 0, im.width, head - 2), fill=colour)
        d.text((8, 6), title, fill=(255, 255, 255))
        for i, t in enumerate(lines):
            d.text((8, 24 + 15 * i), t[:150], fill=(210, 220, 255))
        cell.paste(im, (0, head))
        return cell

    def closeups(self, r: dict[str, Any], regions: list[tuple[str, str, str]], radius: int = 95,
                 zoom: float = 1.7) -> Image.Image:
        """Close-ups centred on joints: (title, bone, 'head'|'tail')."""
        img = on_bg(r["image"], BG).convert("RGB")
        J, T = r["solve"]["joints_posed"], r["solve"]["bone_tails_posed"]
        pad = r["pad"]
        cells = []
        for title, bone, end in regions:
            x, y = (J if end == "head" else T)[bone]
            b = (int(x + pad - radius), int(y + pad - radius), int(x + pad + radius), int(y + pad + radius))
            size = int(2 * radius * zoom)
            c = Image.new("RGB", (size, size + 20), "white")
            ImageDraw.Draw(c).text((4, 4), title, fill=(0, 0, 0))
            c.paste(img.crop(b).resize((size, size), Image.LANCZOS), (0, 20))
            cells.append(c)
        return _row(cells)


def _row(cells: list[Image.Image], gap: int = 8, bg=(255, 255, 255)) -> Image.Image:
    W = sum(c.width for c in cells) + gap * (len(cells) - 1)
    H = max(c.height for c in cells)
    out = Image.new("RGB", (W, H), bg)
    x = 0
    for c in cells:
        out.paste(c, (x, 0))
        x += c.width + gap
    return out


def _col(cells: list[Image.Image], gap: int = 10, bg=(236, 238, 242)) -> Image.Image:
    W = max(c.width for c in cells)
    H = sum(c.height for c in cells) + gap * (len(cells) - 1)
    out = Image.new("RGB", (W, H), bg)
    y = 0
    for c in cells:
        out.paste(c, (0, y))
        y += c.height + gap
    return out


def _save(img: Image.Image, path: Path, max_w: int = 2400) -> str:
    if img.width > max_w:
        img = img.resize((max_w, int(img.height * max_w / img.width)), Image.LANCZOS)
    img.save(path, quality=86, optimize=True)
    return path.name


def _short(res: dict[str, Any]) -> dict[str, Any]:
    s = res["solve"]
    return {"name": res["name"], "phase": res["phase"], "status": res["feasibility"]["status"],
            "missing": [f"{m['asset']} ({m['state']})" for m in res["feasibility"]["missing"]],
            "hand_fallbacks": res["feasibility"].get("hand_fallbacks", []),
            "hands": {k: {kk: v.get(kk) for kk in ("pose", "source", "pending_review", "note")} for k, v in res["hands"].items()},
            "arms": {k: {kk: v.get(kk) for kk in ("mode", "status", "error_px", "flexion_deg", "target", "wrist")}
                     for k, v in s["arms"].items()},
            "contacts": s.get("contacts"), "notes": s.get("notes", []) + s.get("ik_notes", []),
            "corrections_on": [{k: c[k] for k in ("id", "value_deg", "asset_state", "applied", "needs_artwork")}
                               for c in res["correction_switches"] if c["on"]],
            "quality": {k: res["quality"]["numerical"][k] for k in ("passed", "total")} if "quality" in res else None,
            "timing_ms": res["timing_ms"]}


def ik_demo(pkg, out=None, progress=print) -> dict[str, Any]:
    pkg = Path(pkg)
    out = Path(out) if out else pkg / "natural_pose" / "ik_switch"
    (out / "closeups").mkdir(parents=True, exist_ok=True)
    D = Demo(pkg, progress)
    e = D.eng
    res: dict[str, Any] = {"package": pkg.name, "engine_build_s": round(D.build_s, 2), "renders": {},
                           "note": "preview renders of the AutoRig deformer; Moho not run; pending drawings appear "
                                   "only in images labelled PREVIEW; nothing approved here"}
    shots: dict[str, Any] = {}

    def lines(r: dict[str, Any], extra: list[str] | None = None) -> list[str]:
        f = r["feasibility"]
        L = [f"status {f['status']}" + (f" | missing: {', '.join(m['asset'] for m in f['missing'])}" if f["missing"] else "")]
        for s in SIDES:
            h = r["hands"][s]
            L.append(f"hand {s}: {h['pose']} ({h['source']}{', PENDING REVIEW' if h['pending_review'] else ''})"
                     + (f" - {h['note']}" if h.get("note") else ""))
        return L + (extra or [])

    def emit(key: str, title: str, r: dict[str, Any], ik=None, extra=None, close=None, colour=(34, 40, 60)):
        progress(f"  {key}")
        box = D.box([r])
        clean = D.tile(on_bg(r["image"], BG).convert("RGB"), box, title, lines(r, extra), colour)
        diag = D.tile(D.overlay(r, ik), box, title + " - diagnostic (targets, poles, reach, contacts)",
                      lines(r, extra) + [n for n in r["solve"].get("notes", []) + r["solve"].get("ik_notes", [])
                                         if n.startswith(("arm_", "leg_", "pelvis"))][:4], colour)
        shots[key] = {"clean": _save(clean, out / f"{key}.jpg"), "diagnostic": _save(diag, out / f"{key}_diag.jpg")}
        if close:
            shots[key]["closeups"] = "closeups/" + _save(D.closeups(r, close), out / "closeups" / f"{key}.jpg")
        res["renders"][key] = _short(r)
        return r

    ARM_CLOSE = [("wrist r", "hand_r", "head"), ("elbow r", "forearm_r", "head"), ("sleeve cuff r", "forearm_r", "tail"),
                 ("wrist l", "hand_l", "head"), ("elbow l", "forearm_l", "head"), ("sleeve cuff l", "forearm_l", "tail")]
    LEG_CLOSE = [("knee r", "shin_r", "head"), ("knee l", "shin_l", "head"), ("ankle r", "foot_r", "head"),
                 ("ankle l", "foot_l", "head")]

    # 1 standing
    emit("01_standing", "Standing (relaxed; preferred relaxed hand is missing -> reference hand)",
             D.render("relaxed_standing"), close=ARM_CLOSE + LEG_CLOSE)
    # 2 right-arm IK reach
    ik2 = {"arm_r": {"enabled": True, "mode": "ik", "target": D.arm_target("r", 0.62, -0.45), "pole": None, "bend": None}}
    emit("02_right_arm_ik_reach", "Right-arm IK reach (wrist target up and out)", D.render("relaxed_standing", ik=ik2),
         ik2, close=ARM_CLOSE[:3])
    # 3 both arms raised via IK
    ik3 = {f"arm_{s}": {"enabled": True, "mode": "ik", "target": D.arm_target(s, 0.32, -0.88), "bend": None, "pole": None}
           for s in SIDES}
    emit("03_both_arms_raised_ik", "Both arms raised by IK (wrist targets above the shoulders)",
         D.render("relaxed_standing", ik=ik3), ik3, close=ARM_CLOSE)
    # 4 pointing with the pointing hand (pending: preview) and the approved-only fallback
    sw4 = {"hand_r": {"user": "pointing", "auto": False, "keys": [], "offsets": {}}}
    r4a = D.render("pointing_speaking", switches=sw4)
    emit("04_pointing_hand", "PREVIEW (pending review): pointing hand on the pointing arm (arm IK reach)",
               D.render("pointing_speaking", switches=sw4, allow_pending=True), close=ARM_CLOSE[:3], colour=(110, 70, 20))
    res["renders"]["04_pointing_hand_approved_only"] = _short(r4a)
    # 5 running with hand switches: approved-only vs pending fist preview (same phase)
    r5a = D.render("running", 0.3)
    sw5 = {f"hand_{s}": {"user": "fist", "auto": False, "keys": [], "offsets": {}} for s in SIDES}
    r5b = D.render("running", 0.3, switches=sw5, allow_pending=True)
    box = D.box([r5a, r5b])
    img5 = _row([D.tile(on_bg(r5a["image"], BG).convert("RGB"), box, "Running, approved art only (open hands)", lines(r5a)),
                 D.tile(on_bg(r5b["image"], BG).convert("RGB"), box, "PREVIEW (pending review): fist switches", lines(r5b),
                        (110, 70, 20))])
    shots["05_running_hand_switches"] = {"clean": _save(img5, out / "05_running_hand_switches.jpg"),
                                         "closeups": "closeups/" + _save(D.closeups(r5b, ARM_CLOSE),
                                                                         out / "closeups" / "05_running_fists.jpg")}
    res["renders"]["05_running_approved"] = _short(r5a)
    res["renders"]["05_running_fist_preview"] = _short(r5b)
    progress("  05_running_hand_switches")
    # 6 walking with planted feet: six phases, contact markers and lock errors
    walk = [D.render("casual_walk", i / 6) for i in range(6)]
    box = D.box(walk)
    box = (box[0], box[1] + int(0.45 * (box[3] - box[1])), box[2], box[3])  # legs
    cells = []
    for i, r in enumerate(walk):
        c = r["solve"]["contacts"]
        cells.append(D.tile(D.overlay(r, None), box, f"walk phase {i}/6",
                            [f"{s}: {c[s]['state']}" + (f" slide {c[s]['slide_px']:.3f}px lift {c[s]['lift_px']:.2f}px" if c[s]["lock_error_px"] is not None else "")
                             for s in SIDES]))
    shots["06_walking_planted_feet"] = {"clean": _save(_row(cells), out / "06_walking_planted_feet.jpg")}
    res["renders"]["06_walking"] = [_short(r) for r in walk]
    progress("  06_walking_planted_feet")
    # 7 sitting with foot constraints (feet locked where the preset plants them; pelvis lowered)
    it7 = e.plan_and_solve("sitting")[0]
    ik7 = {f"leg_{s}": {"enabled": True, "mode": "ik", "target": list(it7.legs[s].ankle), "lock": True, "pole": None}
           for s in SIDES}
    emit("07_sitting_foot_constraints", "Sitting (front-view approximation) with both feet locked",
         D.render("sitting", ik=ik7), ik7, close=LEG_CLOSE)
    # 8 deep elbow bend
    ik8 = {"arm_r": {"enabled": True, "mode": "ik", "target": D.arm_target("r", 0.12, 0.36), "bend": None, "pole": None}}
    emit("08_deep_elbow_bend", "Deep elbow bend by IK (no corrective elbow drawing exists: band deformation only)",
         D.render("relaxed_standing", ik=ik8), ik8, close=ARM_CLOSE[:3])
    # 10 IK target/pole diagnostic overlay (mixed: arm IK with explicit pole, leg lifted, pelvis shifted)
    j0 = e.plan_and_solve("relaxed_standing")[2]["joints_posed"]
    ik10 = {"arm_l": {"enabled": True, "mode": "ik", "target": D.arm_target("l", 0.7, -0.2),
                      "pole": D.arm_target("l", 0.25, 0.4), "bend": None},
            "leg_r": {"enabled": True, "mode": "ik", "target": [j0["foot_r"][0] - 15, j0["foot_r"][1] - 60], "lock": False},
            "leg_l": {"enabled": True, "mode": "ik", "target": list(j0["foot_l"]), "lock": True},
            "pelvis_offset": [12.0, 0.0]}
    emit("10_ik_diagnostic", "IK diagnostic: arm target + explicit pole, right foot lifted, left foot locked, pelvis shifted",
         D.render("relaxed_standing", ik=ik10), ik10, close=LEG_CLOSE[:2])

    # 9 hand switch comparison sheet (relaxed standing; every pose for both hands; measured attachment)
    progress("  09_hand_switch_sheet")
    lib = e.hand_lib
    poses = list(REQUIRED_HAND_POSES) + list(EXTRA_HAND_POSES)
    rows, table = [], []
    for pose in poses:
        sw = {f"hand_{s}": {"user": pose, "auto": False, "keys": [], "offsets": {}} for s in SIDES}
        avail = any(lib.hands[s].get(pose) and lib.hands[s][pose].available for s in SIDES)
        r = D.render("relaxed_standing", switches=sw, allow_pending=True) if avail else None
        cells = []
        for s in SIDES:
            v = lib.hands[s].get(pose)
            al = lib.alignment(s, pose, e.profile.midline_x)
            rec = {"side": s, "pose": pose, "status": v.status if v else "missing", "rest": al}
            if r is not None and v is not None and v.available:
                lay = next(c for c in r["order"] if c.startswith(f"hand_{s}"))
                pa = posed_hand_alignment(e, r, s, lay, v.measure["attachment"])
                rec["posed"] = pa
                x, y = r["solve"]["joints_posed"][f"hand_{s}"]
                rad = 85
                b = (int(x + r["pad"] - rad), int(y + r["pad"] - 0.6 * rad), int(x + r["pad"] + rad), int(y + r["pad"] + 1.4 * rad))
                im = on_bg(r["image"], BG).convert("RGB").crop(b).resize((2 * rad * 2, 2 * rad * 2), Image.LANCZOS)
                lab = [f"{pose} {s}: {v.status}", f"rest wrist err {al.get('attachment_error_px')} px, width x{al.get('width_ratio')}",
                       f"posed gap {pa.get('sleeve_gap_px')} px, drift {pa.get('posed_attachment_drift_px')} px",
                       ("OK" if al["status"] == "OK" and pa["status"] == "OK" else "FLAG: " + "; ".join(al.get("issues", []) + pa.get("issues", [])))]
            else:
                im = Image.new("RGB", (340, 340), (250, 235, 235))
                ImageDraw.Draw(im).text((110, 160), "NEEDS_ARTWORK", fill=(180, 30, 30))
                lab = [f"{pose} {s}: missing", f"fallback: open_palm (reference hand)"]
            cell = Image.new("RGB", (im.width, im.height + 66), "white")
            d = ImageDraw.Draw(cell)
            for i, t in enumerate(lab):
                d.text((6, 4 + 15 * i), t[:62], fill=(0, 0, 0) if i < 3 else ((30, 120, 40) if t == "OK" else (180, 30, 30)))
            cell.paste(im, (0, 66))
            cells.append(cell)
            table.append(rec)
        rows.append(_row(cells))
    # auto-aligned right fist (alignment tool): the suggested offset applied, re-measured at rest
    aa = lib.auto_align("r", "fist")
    if aa is not None:
        dx, dy = aa["offset_px"]
        piv = e.sk.bone("hand_r").head
        moved = offset_layer(e.variants["hand_r__fist"], piv, dx, dy, 0.0)
        cover = e.variants.get("forearm_r__trim", e.layers["forearm_r"])[..., 3] > 127
        m2 = lib.measure(moved, "r", cover)
        ref = lib.hands["r"]["open_palm"].measure
        err2 = float(np.hypot(*(np.subtract(m2["attachment"], ref["attachment"]))))
        res["auto_align_right_fist"] = {"offset_px": [dx, dy], "rest_error_before_px": aa["error_before_px"],
                                        "search_error_after_px": aa["error_after_px"],
                                        "rest_error_after_px": round(err2, 2),
                                        "area_before_px": lib.hands["r"]["fist"].measure["area_px"] + lib.hands["r"]["fist"].measure["hidden_px"],
                                        "area_after_px": m2["area_px"] + m2["hidden_px"]}
    sheet = _col(rows)
    head = Image.new("RGB", (sheet.width, 40), (34, 40, 60))
    ImageDraw.Draw(head).text((8, 6), "Hand switch comparison (relaxed standing; PREVIEW of pending drawings; right | left). "
                              "Rest error: visible wrist attachment vs the reference hand; posed: gap to the sleeve cuff and "
                              "drift of the attachment from the bone-carried wrist.", fill=(255, 255, 255))
    ImageDraw.Draw(head).text((8, 22), f"Tolerances: rest {ALIGN['attachment_error_px']} px, width {ALIGN['width_ratio']}, "
                              f"gap {ALIGN['sleeve_gap_px']} px, drift {ALIGN['posed_attachment_drift_px']} px", fill=(210, 220, 255))
    shots["09_hand_switch_sheet"] = {"clean": _save(_col([head, sheet], gap=0), out / "09_hand_switch_sheet.jpg", 1800)}
    res["hand_switch_table"] = table

    # animated IK sequence: right wrist target sweeps from hanging to overhead and out; bend side held
    progress("  ik_reach animation")
    seq = []
    N = 20
    for i in range(N):
        a = math.radians(-20 + 200 * i / (N - 1))  # angle from straight down, outward then up
        tgt = D.shoulder("r") + 0.8 * D.L["r"] * np.array([LAT["r"] * math.sin(a), math.cos(a)])
        seq.append([round(float(tgt[0]), 2), round(float(tgt[1]), 2)])
    frames, joints = [], []
    for t in seq:
        ik = {"arm_r": {"enabled": True, "mode": "ik", "target": t, "bend": 1, "pole": None}}
        r = D.render("relaxed_standing", ik=ik)
        frames.append(r["image"])
        joints.append(r["solve"])
    acc = np.zeros(frames[0].shape[:2], bool)
    for f in frames:
        acc |= f[..., 3] > 0
    ys, xs = np.nonzero(acc)
    b = (int(xs.min()) - 16, int(ys.min()) - 16, int(xs.max()) + 16, int(ys.max()) + 16)
    save_animation([f[b[1]:b[3], b[0]:b[2]] for f in frames], 90, out / "ik_reach.gif", scale=0.45, bg=BG)
    shots["ik_reach_animation"] = {"gif": "ik_reach.gif"}
    # continuity metrics on a dense skeleton-only sweep (the GIF frames are sparse)
    dense = []
    for i in range(241):
        a = math.radians(-20 + 200 * i / 240)
        tgt = D.shoulder("r") + 0.8 * D.L["r"] * np.array([LAT["r"] * math.sin(a), math.cos(a)])
        _, pose, info = e.plan_and_solve("relaxed_standing", ik={"arm_r": {"enabled": True, "mode": "ik",
                                                                          "target": list(map(float, tgt)), "bend": 1}})
        dense.append(info)
    steps, flips, errs, prev_side = [], 0, [], None
    for k, info in enumerate(dense):
        a = info["arms"]["r"]
        errs.append(a["error_px"])
        sh = np.asarray(info["joints_posed"]["upper_arm_r"])
        w, el = np.asarray(a["wrist"]) - sh, np.asarray(a["elbow"]) - sh
        side = np.sign(w[0] * el[1] - w[1] * el[0])
        if prev_side is not None and side != prev_side:
            flips += 1
        prev_side = side
        if k:
            P0, P1 = dense[k - 1]["joints_posed"], info["joints_posed"]
            steps.append(max(float(np.hypot(P1[n][0] - P0[n][0], P1[n][1] - P0[n][1])) for n in P1))
    target_step = float(np.hypot(*(np.subtract(seq[1], seq[0])))) / ((240 / (N - 1)))
    res["ik_sequence"] = {"frames": N, "dense_samples": len(dense), "elbow_side_flips": flips,
                          "max_wrist_error_px": round(max(errs), 4), "max_joint_step_px": round(max(steps), 3),
                          "mean_joint_step_px": round(float(np.mean(steps)), 3),
                          "target_step_px": round(target_step, 3),
                          "max_step_over_target_step": round(max(steps) / max(target_step, 1e-9), 2)}

    # performance (this machine)
    res["performance"] = performance(e, D)
    res["shots"] = shots
    res["alignment_tolerances"] = {k: list(v) if isinstance(v, tuple) else v for k, v in ALIGN.items()}
    res["missing_artwork"] = [m for m in e.hand_lib.missing()]
    (out / "results.json").write_text(json.dumps(res, indent=1, default=_jd), encoding="utf-8")
    return res


def _jd(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, tuple):
        return list(o)
    raise TypeError(type(o))


def performance(e, D: Demo | None = None, reps: int = 200) -> dict[str, Any]:
    """Measured timings on this machine (not the user's M1)."""
    import platform

    sol = TwoBoneIKSolver(120.0, 110.0, 1.6, 150.0)
    t = time.perf_counter()
    for i in range(20000):
        sol.solve((0.0, 0.0), (80.0 + i % 50, 120.0), (200.0, 50.0))
    ik_us = (time.perf_counter() - t) / 20000 * 1e6
    sh = np.asarray(e.sk.bone("upper_arm_r").head, float)
    ik = {"arm_r": {"enabled": True, "mode": "ik", "target": [float(sh[0]) - 120, float(sh[1]) - 150], "bend": 1}}
    t = time.perf_counter()
    for _ in range(reps // 4):
        e.plan_and_solve("relaxed_standing", ik=ik)
    solve_ik_ms = (time.perf_counter() - t) / (reps // 4) * 1000
    t = time.perf_counter()
    for _ in range(reps // 4):
        e.plan_and_solve("relaxed_standing")
    solve_ms = (time.perf_counter() - t) / (reps // 4) * 1000
    it, pose, _ = e.plan_and_solve("relaxed_standing", ik=ik)
    sw = {"hand_r": {"user": "fist", "auto": False, "keys": [], "offsets": {}}}
    t = time.perf_counter()
    for _ in range(reps):
        e.resolve_switches(it, True, sw, None)
    switch_ms = (time.perf_counter() - t) / reps * 1000
    t = time.perf_counter()
    for _ in range(5):
        r = e.pose("relaxed_standing", ik=ik, switches=sw, allow_pending=True, skeleton_only=True)
    skel_ms = (time.perf_counter() - t) / 5 * 1000
    full = [e.pose("relaxed_standing", ik=ik, switches=sw, allow_pending=True, visible=D.vis if D else None)["timing_ms"]
            for _ in range(3)]
    return {"machine": f"{platform.machine()} {platform.processor() or ''} (cloud sandbox CPU, not the user's M1)".strip(),
            "two_bone_ik_solve_us": round(ik_us, 2),
            "skeleton_solve_ms": round(solve_ms, 2), "skeleton_solve_with_arm_ik_ms": round(solve_ik_ms, 2),
            "switch_resolution_ms": round(switch_ms, 3),
            "skeleton_only_pose_request_ms": round(skel_ms, 2),
            "full_frame_ms": [f["total"] for f in full], "deformation_and_tears_ms": [f["render_and_tears"] for f in full],
            "corrections_ms": [f["corrections"] for f in full],
            "fps_skeleton_only_capacity": round(1000 / skel_ms, 1), "fps_full_artwork": round(1000 / np.mean([f["total"] for f in full]), 2),
            "target": "30 fps on the M1 for interactive preview: met for skeleton-only previews in the cloud; "
                      "not met for full-artwork renders (CPU deformer). Not measured on the M1."}
