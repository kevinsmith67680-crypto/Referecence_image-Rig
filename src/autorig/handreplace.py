"""Evidence for the sleeve-free hand replacement (``autorig hand-replace-evidence``).

Writes ``<package>/hand_store/evidence_v2/``:

* ``tests/NN_<name>.png``: the ten Step 12 test renders (full figure, both hands close up, the decision);
* ``library_attached.jpg``: all 16 sleeve-free drawings attached to the character (hanging-arm close-up and the
  drawing in the action it is used for);
* ``auto_selection.jpg``: the automatically selected hands across the actions;
* ``before_after.jpg``: original hand vs the archived sleeve-attached library vs the sleeve-free library;
* ``switch_sequence.gif`` / ``.png``: gestures switching while the arm moves (with the selection state);
* ``results.json``: every measurement, per-test checks and the 14 quality gates (Step 13).

Pending-review drawings are *previewed* here (``allow_pending``); nothing is approved. Measurements are geometric
(attachment, overlap, rigidity, unchanged forearm/clothing pixels, switch counts); visual quality is for a person
to judge (``artistic_review_required``)."""

from __future__ import annotations

import copy
import json
import math
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from .handstore import PHASE2_LIBRARY, HandStore, engine_input, posed_seam
from .motion.handselect import AIM_RATE_DEG_S, MIN_HOLD_ACTION_S, HandSelectionState
from .motion.pipeline import cycle_duration_s, engine_for_package, save_animation
from .motion.rigctl import default_ik_state
from .phase2.pose_eval import on_bg

LAT = {"r": -1.0, "l": 1.0}
# overhang: the hand may flare at most this far past the cuff's side at the cuff line (the cover is always drawn
# over the hand, so no hand pixel can lie on the sleeve); pop: the posed wrist-opening anchor's distance from the
# posed cuff point may change at most this much across a switch
SEAM = {"overhang_px": 8.0, "centre_frac": 0.25, "own_white_px": 40, "pop_px": 1.5, "aim_residual_deg": 15.0}


def _font(size: int = 13):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _ik(side: str, target) -> dict[str, Any]:
    d = default_ik_state()[f"arm_{side}"]
    d.update(enabled=True, mode="ik", target=[float(target[0]), float(target[1])])
    return {f"arm_{side}": d}


class _Geo:
    def __init__(self, eng):
        sk = eng.sk
        self.sh = {s: np.asarray(sk.bone(f"upper_arm_{s}").head, float) for s in "rl"}
        self.L1 = {s: sk.bone(f"upper_arm_{s}").length for s in "rl"}
        self.L2 = {s: sk.bone(f"forearm_{s}").length for s in "rl"}
        rest = eng.pose("relaxed_standing", skeleton_only=True)
        self.wrist = {s: eng.solver.K.point(sk.idx[f"hand_{s}"], sk.bone(f"hand_{s}").head, rest["pose"]) for s in "rl"}

    def thumbs(self, s):
        return _ik(s, self.sh[s] + np.array([LAT[s] * (0.25 * self.L1[s] + 0.95 * self.L2[s]), 0.9 * self.L1[s]]))

    def point(self, s):
        L = self.L1[s] + self.L2[s]
        return (_ik(s, self.sh[s] + np.array([LAT[s] * 0.95 * L, -0.12 * L])),
                (self.sh[s] + np.array([LAT[s] * 3.0 * L, -0.3 * L])).tolist())

    def grip(self, s):
        return (self.wrist[s] + np.array([LAT[s] * 0.10 * self.L2[s], 0.42 * self.L2[s]])).tolist()


def test_configs(geo: _Geo) -> list[dict[str, Any]]:
    """The Step 12 tests: preset, hand actions, targets and IK, and the gestures each side should end up with."""
    ikl, far_l = geo.point("l")
    return [
        {"id": "01_relaxed_standing", "title": "Standing with relaxed hands", "kw": {"name": "relaxed_standing"},
         "expect": {"r": ["relaxed"], "l": ["relaxed"]}},
        {"id": "02_walking", "title": "Walking with natural hands", "kw": {"name": "casual_walk", "phase": 0.25},
         "expect": {"r": ["relaxed", "partially_closed"], "l": ["relaxed", "partially_closed"]}, "cycle": "casual_walk"},
        {"id": "03_running_fists", "title": "Running with fists", "kw": {"name": "running", "phase": 0.25},
         "expect": {"r": ["fist"], "l": ["fist"]}, "cycle": "running"},
        {"id": "04_pointing_right_hand", "title": "Pointing with the right hand", "kw": {"name": "pointing_speaking"},
         "expect": {"r": ["pointing"], "l": ["relaxed"]}},
        {"id": "05_pointing_left_hand", "title": "Pointing with the left hand",
         "kw": {"name": "relaxed_standing", "ik": ikl, "hand_actions": {"l": "pointing"}, "hand_targets": {"l": far_l}},
         "expect": {"r": ["relaxed"], "l": ["pointing"]}},
        {"id": "06_shrugging_open_palms", "title": "Shrugging with open palms", "kw": {"name": "one_hand_shrug"},
         "expect": {"r": ["open_palm", "cupped"], "l": ["open_palm", "cupped"]}},
        {"id": "07_gripping_object", "title": "Gripping an object",
         "kw": {"name": "relaxed_standing", "hand_actions": {"r": "gripping"}, "hand_targets": {"r": geo.grip("r")}},
         "expect": {"r": ["gripping"], "l": ["relaxed"]}, "object": "r"},
        {"id": "08_thumbs_up", "title": "Thumbs-up gesture",
         "kw": {"name": "relaxed_standing", "ik": geo.thumbs("r"), "hand_actions": {"r": "thumbs_up"}},
         "expect": {"r": ["thumbs_up"], "l": ["relaxed"]}},
        {"id": "09_hands_near_hips", "title": "Hands resting near the hips", "kw": {"name": "hands_on_hips"},
         "expect": {"r": ["relaxed", "cupped"], "l": ["relaxed", "cupped"]}},
    ]


# -- measurement -----------------------------------------------------------------------------------------------
def hand_metrics(eng, res: dict[str, Any], side: str) -> dict[str, Any]:
    h = res["hands"][side]
    v = eng.hand_lib.hands[side].get(h["pose"])
    if h.get("preview_override"):  # a forced drawing (original hand, or one candidate in the library sheet)
        v = next((c for cs in eng.hand_lib.candidates[side].values() for c in cs if c.layer == h["layer"]), v)
        h = {**h, "pose": v.pose if v is not None else h["pose"], "cid": v.cid if v is not None else h["cid"],
             "source": "preview"}
    layer = h.get("layer") or (f"hand_{side}" if h["drawing"] == "open" else f"hand_{side}__{h['drawing']}")
    seam = posed_seam(res, eng, side, layer)
    meta = (v.meta or {}) if v is not None else {}
    hd = meta.get("handedness") or {}
    side_name = "right" if side == "r" else "left"
    sel = h.get("selection") or {}
    aim = (res.get("hand_selection", {}).get("decisions", {}).get(side) or {}).get("aim_applied")
    stats = (res["render"].get("stats") or {}).get(layer) if "render" in res else None
    pl = (v.placement or {}) if v is not None else {}
    out = {"pose": h["pose"], "cid": h["cid"], "layer": layer, "source": h["source"], "status": h.get("status") or
           (v.status if v is not None else None), "action": sel.get("action"), "reason": sel.get("reason"),
           "score": sel.get("score"), "handedness": hd or ("character's own hand" if h["cid"] == "original" else None),
           "aim": aim, "seam": seam, "rigid": stats, "scale": (pl.get("final") or {}).get("scale"),
           "side_scale": pl.get("side_scale"), "wrist_bend_deg": res["solve"]["anatomical"][f"hand_{side}_bend"]}
    cuff = seam.get("cuff_width_px") or 0.0
    centred = (seam.get("covered") and abs(seam["hand_center_q_px"] - seam["cuff_center_q_px"]) <= SEAM["centre_frac"] * cuff)
    if not h["cid"].startswith("store:"):  # the character's own hand (or a Phase 2 drawing): unchanged artwork
        out["checks"] = {"handedness": True, "wrist_orientation": abs(out["wrist_bend_deg"]) <= 60.0}
        out["note"] = "the character's original hand artwork: attachment checks apply to replacement drawings"
        return out
    chk = {
        "handedness": h["cid"] == "original" or not hd or hd.get("anatomical") == side_name,
        "attachment": bool(seam.get("status") == "OK" and centred),
        "no_sleeve_duplication": seam.get("own_white_px_near_wrist") is None or seam["own_white_px_near_wrist"] <= SEAM["own_white_px"],
        "no_large_gap": bool(seam.get("covered")) and seam.get("fill", 0) >= 0.5,
        "no_excessive_overlap": max(seam.get("overhang_px") or [0, 0]) <= SEAM["overhang_px"],
        "no_scaling_distortion": (stats is None or stats.get("rigid") or
                                  (abs(stats.get("det_min", 1) - 1) < 0.02 and abs(stats.get("det_max", 1) - 1) < 0.02))
                                 and (out["scale"] is None or abs(out["scale"] - (out["side_scale"] or out["scale"])) < 1e-6),
        "wrist_orientation": abs(out["wrist_bend_deg"]) <= 60.0 and (not aim or not aim.get("measured")
                                                                     or abs(aim["residual_deg"]) <= SEAM["aim_residual_deg"]),
    }
    out["checks"] = chk
    return out


def preserved_outside_hands(eng, a: dict[str, Any], b: dict[str, Any], factor: float = 1.45) -> dict[str, Any]:
    """Pixels that differ between two renders of the same pose outside both hands' zones (disc of ``factor`` x
    the hand bone length around each posed wrist, both renders): 0 means clothing and face are unchanged."""
    ia, ib = a["image"].astype(np.int16), b["image"].astype(np.int16)
    if ia.shape != ib.shape:
        return {"status": "UNCHECKED"}
    H, W = ia.shape[:2]
    yy, xx = np.mgrid[0:H, 0:W]
    zone = np.zeros((H, W), bool)
    for r in (a, b):
        pad = r["pad"]
        for s in "rl":
            jx, jy = r["solve"]["joints_posed"][f"hand_{s}"]
            tx, ty = r["solve"]["bone_tails_posed"][f"hand_{s}"]
            L = eng.sk.bone(f"hand_{s}").length
            for cx, cy in ((jx, jy), ((jx + tx) / 2, (jy + ty) / 2)):
                zone |= np.hypot(xx - cx - pad, yy - cy - pad) <= factor * L
    for r in (a, b):  # and every pixel either render's hand layers cover (fingers can reach past the discs)
        for s in "rl":
            h = r["hands"][s]
            lay = h.get("layer") or (f"hand_{s}" if h["drawing"] == "open" else f"hand_{s}__{h['drawing']}")
            for k, al in r["render"]["alphas"].items():
                if (k == lay or k.startswith(lay + "__off")) and al.shape == zone.shape:
                    zone |= ndimage.binary_dilation(al, iterations=6)  # + the antialiased fringe
    diff = np.abs(ia - ib).max(-1) > 8
    out_d = diff & ~zone
    owners: dict[str, int] = {}
    ys, xs = np.nonzero(out_d)
    for r in (a, b):
        top, order = r["render"]["top"], r["order"]
        for y, x in zip(ys, xs):
            k = order[top[y, x]] if top[y, x] >= 0 else "background"
            owners[k] = owners.get(k, 0) + 1
    # changes along the arms (within 50 px of the posed upper arm / forearm segments): the forearm under a switch
    # drawing is Phase 2's trimmed forearm (``forearm_<side>__trim``, used under every switch drawing since Phase 3.2)
    # plus the wrist-region cleanup (``__hscuff``); their edge antialiasing differs from the original forearm layer.
    # Everything else (torso, legs, face, hair away from the arms) must be unchanged.
    arm = np.zeros((H, W), bool)
    pad = a["pad"]
    for r in (a, b):
        for s in "rl":
            for bn in (f"upper_arm_{s}", f"forearm_{s}"):
                p0 = np.asarray(r["solve"]["joints_posed"][bn], float) + pad
                p1 = np.asarray(r["solve"]["bone_tails_posed"][bn], float) + pad
                v = p1 - p0
                L2 = max(float(v @ v), 1e-9)
                tt = np.clip(((xx - p0[0]) * v[0] + (yy - p0[1]) * v[1]) / L2, 0, 1)
                arm |= np.hypot(xx - (p0[0] + tt * v[0]), yy - (p0[1] + tt * v[1])) <= 50.0
    body = out_d & ~arm
    return {"changed_px_outside_hands": int(out_d.sum()), "changed_px_in_hand_zones": int((diff & zone).sum()),
            "owners_outside": owners, "changed_px_along_arms": int((out_d & arm).sum()),
            "clothing_or_face_px_changed": int(body.sum()),
            "note": "changes along the arms are the forearm under switch drawings (Phase 2 trim + wrist cleanup); the "
                    "torso, legs, face and hair must not change",
            "zone": f"both renders' hand pixels (+6 px) and discs of {factor} x hand length around each posed wrist "
                    "and hand centre (the cuff region)"}


# -- drawing helpers ---------------------------------------------------------------------------------------------
def _full(res: dict[str, Any], h: int = 560, marks: list | None = None) -> Image.Image:
    im = on_bg(res["image"])
    d = ImageDraw.Draw(im)
    pad = res["pad"]
    for (x, y), kind in marks or []:
        if kind == "object":  # an annotation: a held rod at the grip target (not character artwork)
            d.rounded_rectangle([x + pad - 4, y + pad - 34, x + pad + 4, y + pad + 34], 3, fill=(150, 110, 60),
                                outline=(70, 45, 20), width=2)
        else:
            d.ellipse([x + pad - 7, y + pad - 7, x + pad + 7, y + pad + 7], outline=(0, 160, 60), width=3)
    ys, xs = np.nonzero(res["image"][..., 3] > 0)
    im = im.crop((int(xs.min()) - 24, int(ys.min()) - 24, int(xs.max()) + 24, int(ys.max()) + 24))
    k = h / im.height
    return im.resize((int(im.width * k), h), Image.Resampling.LANCZOS)


def _close(res: dict[str, Any], side: str, r: int = 72, size: int = 210, marks: list | None = None) -> Image.Image:
    pad = res["pad"]
    jx, jy = res["solve"]["joints_posed"][f"hand_{side}"]
    tx, ty = res["solve"]["bone_tails_posed"][f"hand_{side}"]
    cx, cy = (jx + 2 * tx) / 3 + pad, (jy + 2 * ty) / 3 + pad
    im = on_bg(res["image"])
    d = ImageDraw.Draw(im)
    for (x, y), kind in marks or []:
        if kind == "object":
            d.rounded_rectangle([x + pad - 4, y + pad - 34, x + pad + 4, y + pad + 34], 3, fill=(150, 110, 60),
                                outline=(70, 45, 20), width=2)
    return im.crop((int(cx - r), int(cy - r), int(cx + r), int(cy + r))).resize((size, size), Image.Resampling.LANCZOS)


def _caption(img: Image.Image, lines: list[str], width: int | None = None, fs: int = 13) -> Image.Image:
    f = _font(fs)
    W = width or img.width
    H = img.height + 8 + (fs + 4) * len(lines)
    out = Image.new("RGB", (W, H), (255, 255, 255))
    out.paste(img, ((W - img.width) // 2, 0))
    d = ImageDraw.Draw(out)
    for i, t in enumerate(lines):
        d.text((6, img.height + 4 + (fs + 4) * i), t, fill=(150, 20, 20) if t.startswith("!") else (25, 25, 35), font=f)
    return out


def _grid(tiles: list[Image.Image], cols: int, gap: int = 10, title: str | None = None) -> Image.Image:
    rows = [tiles[i:i + cols] for i in range(0, len(tiles), cols)]
    cw = max(t.width for t in tiles)
    rh = [max(t.height for t in r) for r in rows]
    top = 34 if title else 0
    out = Image.new("RGB", (cols * (cw + gap) + gap, top + sum(rh) + gap * (len(rows) + 1)), (246, 246, 246))
    d = ImageDraw.Draw(out)
    if title:
        d.text((gap, 10), title, fill=(20, 20, 30), font=_font(15))
    y = top + gap
    for r, h in zip(rows, rh):
        x = gap
        for t in r:
            out.paste(t, (x, y))
            x += cw + gap
        y += h + gap
    return out


def _decision_lines(res: dict[str, Any], side: str) -> list[str]:
    h = res["hands"][side]
    sel = h.get("selection") or {}
    aim = (res.get("hand_selection", {}).get("decisions", {}).get(side) or {}).get("aim_applied") or {}
    nm = {"r": "right", "l": "left"}[side]
    rank = ", ".join(f"{x['pose']} {x['total']:.2f}" for x in (sel.get("ranking") or [])[:3])
    out = [f"{nm}: {sel.get('action')} -> {h['pose']} [{h['source']}, {h['cid'].split('/')[-1]}]", f"  top: {rank}"]
    if aim.get("measured"):
        out.append(f"  wrist aim {aim['applied_deg']:+.1f} deg (residual {aim['residual_deg']:+.1f})")
    return out


# -- main ----------------------------------------------------------------------------------------------------------
def replacement_evidence(pkg, out=None, progress=print) -> dict[str, Any]:
    pkg = Path(pkg)
    out = Path(out) if out else pkg / "hand_store" / "evidence_v2"
    (out / "tests").mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    eng, vis = engine_for_package(pkg)
    progress(f"engine {time.time() - t0:.1f}s")
    libs = eng.hand_store.get("libraries", {})
    new_lib = next((k for k, v in sorted(libs.items(), key=lambda kv: -(kv[1].get("order") or 0))
                    if v.get("sleeve") == "none" and v["status"] == "active"), None)
    geo = _Geo(eng)
    results: dict[str, Any] = {"package": pkg.name, "library": new_lib, "libraries": libs, "allow_pending": True,
                               "note": "pending-review drawings are previewed, not approved", "tests": [],
                               "artistic_review_required": True}
    hs_new = eng.hand_store

    # -- the Step 12 tests ----------------------------------------------------------------------------------
    sheet_tiles = []
    for cfg in test_configs(geo):
        kw = dict(cfg["kw"])
        name = kw.pop("name")
        res = eng.pose(name, allow_pending=True, visible=vis, **kw)
        base = eng.pose(name, allow_pending=True, visible=vis, **kw, hand_layers={"r": "hand_r", "l": "hand_l"})
        hm = {s: hand_metrics(eng, res, s) for s in "rl"}
        for s in "rl":
            hm[s]["checks"]["gesture"] = res["hands"][s]["pose"] in cfg["expect"][s]
        keep = preserved_outside_hands(eng, res, base)
        marks = [((t[0], t[1]), "object" if cfg.get("object") == s else "target")
                 for s, t in (kw.get("hand_targets") or {}).items()]
        rec = {"id": cfg["id"], "title": cfg["title"], "preset": name,
               "config": {k: v for k, v in kw.items() if k != "ik"} | ({"ik": {k: v["target"] for k, v in kw["ik"].items()}}
                                                                    if kw.get("ik") else {}),
               "expect": cfg["expect"], "hands": hm, "preserved": keep, "grip": res["hand_selection"].get("grip"),
               "feasibility": res["feasibility"]["status"]}
        if cfg.get("cycle"):
            rec["cycle_switching"] = cycle_switches(eng, cfg["cycle"])
        ok = all(all(v for v in hm[s]["checks"].values()) for s in "rl") and keep.get("clothing_or_face_px_changed") == 0 \
            and (not rec.get("cycle_switching") or rec["cycle_switching"]["switches_after_first_frame"] == 0)
        rec["status"] = "OK" if ok else "CHECK"
        fig = _full(res, 560, marks)
        closes = [_close(res, s, marks=marks) for s in "rl"]
        strip = Image.new("RGB", (fig.width + 2 * 220 + 20, 560), (255, 255, 255))
        strip.paste(fig, (0, 0))
        strip.paste(closes[0], (fig.width + 10, 40))
        strip.paste(closes[1], (fig.width + 230, 40))
        d = ImageDraw.Draw(strip)
        d.text((fig.width + 10, 18), "right hand (image left)", fill=(40, 40, 60), font=_font(12))
        d.text((fig.width + 230, 18), "left hand (image right)", fill=(40, 40, 60), font=_font(12))
        bad = [f"! {s}: {k}" for s in "rl" for k, v in hm[s]["checks"].items() if not v]
        lines = [f"{cfg['title']} ({name}{', IK' if kw.get('ik') else ''})"] + _decision_lines(res, "r") + \
            _decision_lines(res, "l") + [f"pixels changed vs the original hands, outside the hands: torso/legs/face "
                                     f"{keep.get('clothing_or_face_px_changed')}, along the arms {keep.get('changed_px_along_arms')}"] + bad
        if rec.get("grip"):
            g = next(iter(rec["grip"].values()))
            lines.append(f"grip centre {g.get('error_px')} px from the target (arm IK)")
        if rec.get("cycle_switching"):
            c = rec["cycle_switching"]
            lines.append(f"cycle: {c['frames']} frames, hand switches after the first frame: {c['switches_after_first_frame']}")
        img = _caption(strip, lines, fs=13)
        img.save(out / "tests" / f"{cfg['id']}.png", optimize=True)
        rec["file"] = f"tests/{cfg['id']}.png"
        results["tests"].append(rec)
        sheet_tiles.append(_caption(_full(res, 360, marks), [cfg["title"], *[ln for ln in _decision_lines(res, "r")[:1]],
                                                            *[ln for ln in _decision_lines(res, "l")[:1]]], width=300, fs=11))
        progress(f"{cfg['id']}: {rec['status']} ({time.time() - t0:.0f}s)")

    # -- test 10: switching gestures during arm movement ----------------------------------------------------
    seq = switch_sequence(eng, vis, geo, out)
    results["tests"].append(seq["record"])
    sheet_tiles.append(_caption(seq["thumb"], ["Switching during arm movement", seq["record"]["summary"]], width=300, fs=11))
    progress(f"10 sequence ({time.time() - t0:.0f}s)")
    _grid(sheet_tiles, 5, title="Automatic hand selection across actions (NaturalHandSelectionEngine; pending drawings "
                                "previewed, nothing approved)").save(out / "auto_selection.jpg", quality=88)

    # -- all 16 drawings attached ----------------------------------------------------------------------------
    results["library_sheet"] = library_sheet(eng, vis, geo, out, new_lib)
    progress(f"library sheet ({time.time() - t0:.0f}s)")

    # -- before / after --------------------------------------------------------------------------------------
    results["before_after"] = before_after(eng, vis, pkg, out, libs, new_lib)
    eng.set_hand_store(hs_new, eng.approvals)
    progress(f"before/after ({time.time() - t0:.0f}s)")

    # -- IK, garments, overrides, persistence, preservation -------------------------------------------------
    results["ik"] = ik_checks(eng, vis, geo)
    results["garments"] = garment_checks(eng, vis)
    results["overrides"] = override_checks(eng)
    results["persistence"] = persistence_check(pkg, new_lib)
    results["preservation"] = preservation_check(pkg)
    results["extraction"] = extraction_summary(pkg, new_lib)
    results["missing_and_unsuitable"] = missing_report(eng)
    results["gates"] = gates(results, eng, new_lib)
    results["elapsed_s"] = round(time.time() - t0, 1)
    (out / "results.json").write_text(json.dumps(results, indent=1, default=_jd))
    progress(f"done in {results['elapsed_s']}s")
    return results


def _jd(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def cycle_switches(eng, name: str, frames: int = 16) -> dict[str, Any]:
    """Run a gait cycle frame by frame with the selection state carried over: hand switches after the first
    frame (the hands must not flip while the arms swing)."""
    state = {s: HandSelectionState() for s in "rl"}
    T = cycle_duration_s(name)
    chosen = []
    for i in range(frames + 1):
        ph = (i / frames) % 1.0
        r = eng.pose(name, ph, allow_pending=True, skeleton_only=True, hand_state=state, hand_time=i * T / frames)
        chosen.append({s: r["hands"][s]["pose"] for s in "rl"})
    sw = sum(1 for i in range(1, len(chosen)) for s in "rl" if chosen[i][s] != chosen[i - 1][s])
    return {"frames": frames + 1, "switches_after_first_frame": sw, "hands": chosen[0],
            "history": {s: state[s].history for s in "rl"}}


def switch_sequence(eng, vis, geo: _Geo, out: Path, fps: int = 12) -> dict[str, Any]:
    """The right arm rises from hanging to pointing and comes back; the gesture intention changes at the moments an
    animator would key it (raise starts -> pointing, lowering starts -> relaxed). The selection state is carried
    from frame to frame. Measured: switches, the hand's visible attachment relative to the cuff across each switch
    (pop), the wrist joint path (continuity), and a flicker test (the action toggling every frame)."""
    s = "r"
    L = geo.L1[s] + geo.L2[s]
    w0 = np.asarray(geo.wrist[s], float)
    w1 = geo.sh[s] + np.array([LAT[s] * 0.92 * L, -0.32 * L])
    far = geo.sh[s] + np.array([LAT[s] * 3.0 * L, -0.4 * L])
    n = 36

    def ease(u):
        u = min(max(u, 0.0), 1.0)
        return u * u * (3 - 2 * u)
    sched = []
    for k in range(n):
        if k < 8:
            u, act = 0.0, "relaxed_standing"
        elif k < 18:
            u, act = ease((k - 8) / 9), ("pointing" if k >= 10 else "relaxed_standing")
        elif k < 26:
            u, act = 1.0, "pointing"
        elif k < 34:
            u, act = 1.0 - ease((k - 26) / 7), ("relaxed_standing" if k >= 27 else "pointing")
        else:
            u, act = 0.0, "relaxed_standing"
        sched.append((u, act))
    state = {x: HandSelectionState() for x in "rl"}
    frames, recs, prev_w = [], [], None
    for k, (u, act) in enumerate(sched):
        tgt = (1 - u) * w0 + u * w1
        r = eng.pose("relaxed_standing", allow_pending=True, visible=vis, ik=_ik(s, tgt),
                     hand_actions={s: act}, hand_targets={s: far.tolist()} if act == "pointing" else None,
                     hand_state=state, hand_time=k / fps)
        h = r["hands"][s]
        layer = h["layer"]
        seam = posed_seam(r, eng, s, layer)
        wj = np.asarray(r["solve"]["joints_posed"][f"hand_{s}"], float)
        anc = anchor_to_cuff(eng, r, s)
        recs.append({"frame": k, "t": round(k / fps, 3), "action": act, "pose": h["pose"], "source": h["source"],
                     "reason": h.get("selection", {}).get("reason"), "wrist": wj.round(2).tolist(),
                     "wrist_step_px": None if prev_w is None else round(float(np.hypot(*(wj - prev_w))), 2),
                     "visible_centre_offset_px": None if not seam.get("covered") else round(seam["hand_center_q_px"] - seam["cuff_center_q_px"], 2),
                     "anchor_to_cuff_px": anc,
                     "aim": (r["hand_selection"]["decisions"][s].get("aim_applied") or {}).get("applied_deg")})
        prev_w = wj
        frames.append((r["image"], f"frame {k:02d}  action {act.replace('_', ' ')}  hand {h['pose']}"))
    switches = [i for i in range(1, n) if recs[i]["pose"] != recs[i - 1]["pose"]]
    pops = []
    for i in switches:
        a, b = recs[i - 1]["anchor_to_cuff_px"], recs[i]["anchor_to_cuff_px"]
        if a is not None and b is not None:
            pops.append({"frame": i, "from": recs[i - 1]["pose"], "to": recs[i]["pose"],
                         "anchor_change_px": round(abs(b - a), 3), "wrist_step_px": recs[i]["wrist_step_px"],
                         "aim_before": recs[i - 1]["aim"], "aim_after": recs[i]["aim"]})
    aims = [r["aim"] or 0.0 for r in recs]
    aim_steps = [abs(aims[i] - aims[i - 1]) for i in range(1, n)]
    steps = [r["wrist_step_px"] for r in recs if r["wrist_step_px"] is not None]
    # flicker: the action toggles every frame for 2 s with the arm held at the pointing position
    st2 = {x: HandSelectionState() for x in "rl"}
    flick = []
    for k in range(24):
        act = "pointing" if k % 2 == 0 else "relaxed_standing"
        r = eng.pose("relaxed_standing", allow_pending=True, skeleton_only=True, ik=_ik(s, w1), hand_actions={s: act},
                     hand_targets={s: far.tolist()} if act == "pointing" else None, hand_state=st2, hand_time=k / fps)
        flick.append(r["hands"][s]["pose"])
    fsw = sum(1 for i in range(1, len(flick)) if flick[i] != flick[i - 1])
    al = np.zeros(frames[0][0].shape[:2], bool)
    for img, _ in frames:
        al |= img[..., 3] > 0
    ys, xs = np.nonzero(al)
    box = (int(xs.min()) - 20, int(ys.min()) - 40, int(xs.max()) + 20, int(ys.max()) + 20)
    rgba = [np.ascontiguousarray(img[box[1]:box[3], box[0]:box[2]]) for img, _ in frames]
    save_animation(rgba, 1000 / fps, out / "switch_sequence.gif", scale=0.6, labels=[t for _, t in frames])
    keys = [0, 9, 10, 17, 26, 27, 33, 35]
    tiles = []
    for k in keys:
        im = on_bg(rgba[k])
        im = im.resize((int(im.width * 0.5), int(im.height * 0.5)), Image.Resampling.LANCZOS)
        tiles.append(_caption(im, [frames[k][1].replace("  ", " | ")], fs=11))
    strip = _grid(tiles, len(keys), gap=6, title="Gesture switching during arm movement (right arm IK; the intention "
                                                 "changes at frames 10 and 27; selection state carried frame to frame)")
    strip.save(out / "switch_sequence.png", optimize=True)
    frames = rgba
    summary = (f"{len(switches)} switches (frames {switches}); wrist anchor change at a switch "
               f"{max([p['anchor_change_px'] for p in pops] or [0]):.2f} px; max wrist aim step {max(aim_steps):.1f} deg/frame; "
               f"flicker test {fsw} switches/24 frames")
    rec = {"id": "10_switching_during_arm_movement", "title": "Switching gestures during arm movement",
           "frames": recs, "switch_frames": switches, "pops": pops, "summary": summary,
           "wrist_path": {"max_step_px": max(steps), "mean_step_px": round(float(np.mean(steps)), 2)},
           "wrist_aim": {"max_step_deg_per_frame": round(max(aim_steps), 2), "rate_limit_deg_s": AIM_RATE_DEG_S,
                         "limit_per_frame_deg": round(AIM_RATE_DEG_S / fps, 2)},
           "flicker": {"frames": 24, "toggling": "pointing/relaxed every frame", "switches": fsw,
                       "max_allowed": int(2.0 / MIN_HOLD_ACTION_S) + 1, "sequence": flick},
           "file": "switch_sequence.png", "animation": "switch_sequence.gif"}
    ok = len(switches) == 2 and all(p["anchor_change_px"] <= SEAM["pop_px"] for p in pops) and \
        fsw <= rec["flicker"]["max_allowed"] and max(aim_steps) <= AIM_RATE_DEG_S / fps + 0.05
    rec["status"] = "OK" if ok else "CHECK"
    th = on_bg(frames[17])
    thumb = th.resize((int(th.width * 360 / th.height), 360), Image.Resampling.LANCZOS)
    return {"record": rec, "thumb": thumb}


def anchor_to_cuff(eng, res: dict[str, Any], side: str) -> float | None:
    """Distance (px) between the active stored drawing's posed wrist-opening anchor (its placement point carried by
    the hand bone) and the posed cuff point (the character's cuff edge carried by the forearm). Constant across a
    switch means the new drawing attaches exactly where the old one did."""
    from .handstore import Placement  # noqa: PLC0415

    h = res["hands"][side]
    v = next((c for cs in eng.hand_lib.candidates[side].values() for c in cs if c.layer == h.get("layer")), None)
    if v is None or not v.placement or "final" not in v.placement:
        return None
    fin, ref = v.placement["final"], v.placement["reference_wrist"]
    a = eng.solver.K.point(eng.sk.idx[f"hand_{side}"], np.asarray(fin["point"], float), res["pose"])
    c = eng.solver.K.point(eng.sk.idx[f"forearm_{side}"], np.asarray(ref["edge"], float), res["pose"])
    del Placement
    return round(float(np.hypot(*(a - c))), 3)


def _action_for_pose(pose: str, side: str, geo: _Geo) -> dict[str, Any]:
    if pose == "relaxed":
        return {"name": "relaxed_standing"}
    if pose == "open_palm":
        return {"name": "one_hand_shrug"}
    if pose == "fist":
        return {"name": "running", "phase": 0.25}
    if pose == "pointing":
        ik, far = geo.point(side)
        return {"name": "relaxed_standing", "ik": ik, "hand_actions": {side: "pointing"}, "hand_targets": {side: far}}
    if pose == "thumbs_up":
        return {"name": "relaxed_standing", "ik": geo.thumbs(side), "hand_actions": {side: "thumbs_up"}}
    if pose == "gripping":
        return {"name": "relaxed_standing", "hand_actions": {side: "gripping"}, "hand_targets": {side: geo.grip(side)}}
    if pose == "palm_down":
        return {"name": "sitting"}
    return {"name": "hands_on_hips"}


def library_sheet(eng, vis, geo: _Geo, out: Path, lib: str | None) -> dict[str, Any]:
    """Every drawing of the sleeve-free library attached to the character: at the hanging wrist (close-up, the
    attachment) and in an action that uses it (the drawing forced on that side with an explicit switch choice)."""
    tiles, recs = [], []
    for s in "rl":
        for pose, cands in eng.hand_lib.candidates[s].items():
            for v in cands:
                if not v.cid.startswith(f"store:{lib}/"):
                    continue
                rest = eng.pose("relaxed_standing", allow_pending=True, visible=vis, hand_layers={s: v.layer})
                kw = _action_for_pose(pose, s, geo)
                name = kw.pop("name")
                sw = {f"hand_{s}": {"user": pose, "auto": False, "keys": [], "offsets": {}}}
                act = eng.pose(name, allow_pending=True, visible=vis, switches=sw, hand_layers={s: v.layer}, **kw)
                m = hand_metrics(eng, rest, s)
                ma = hand_metrics(eng, act, s)
                pl = (v.placement or {}).get("params") or {}
                a = _close(rest, s, r=66, size=200)
                b = _full(act, 260, [((t[0], t[1]), "object" if pose == "gripping" else "target")
                                     for _, t in (kw.get("hand_targets") or {}).items()])
                tile = Image.new("RGB", (200 + 8 + b.width, 260), (255, 255, 255))
                tile.paste(a, (0, 30))
                tile.paste(b, (208, 0))
                flags = (v.meta or {}).get("flags") or []
                lines = [f"{'right' if s == 'r' else 'left'} {pose}  ({v.cid.split('/')[-1]}, {v.status})",
                         f"wrist {pl.get('opening', '?').replace('_', ' ')} conf {pl.get('opening_confidence')}  fit {pl.get('wrist_fit')}",
                         f"rest seam: overhang {m['seam'].get('overhang_px')} own white {m['seam'].get('own_white_px_near_wrist')}"]
                lines += [f"! {f[:60]}" for f in flags[:2]]
                tiles.append(_caption(tile, lines, fs=11))
                recs.append({"side": s, "pose": pose, "cid": v.cid, "status": v.status, "alignment": pl,
                             "rest_seam": m["seam"], "rest_checks": m["checks"], "action": name,
                             "action_checks": ma["checks"], "flags": flags})
    _grid(tiles, 4, title=f"All {len(recs)} sleeve-free drawings attached to the schoolboy (left: hanging wrist close-up; "
                          "right: in an action; pending review, previewed)").save(out / "library_attached.jpg", quality=88)
    return {"file": "library_attached.jpg", "drawings": recs}


def before_after(eng, vis, pkg: Path, out: Path, libs: dict[str, Any], new_lib: str | None) -> dict[str, Any]:
    """Same poses with (a) the character's original hand, (b) the archived sleeve-attached library (restored
    temporarily, with the Phase 2 sheet hands, exactly as before this change) and (c) the sleeve-free library."""
    old_libs = [k for k, v in libs.items() if v.get("sleeve") == "attached" and not v.get("pseudo")]
    base_variants = {k: v for k, v in eng.variants.items() if "__hs." not in k and "__hscuff" not in k and "__off" not in k}
    from .editor.project import EditorProject

    st = copy.deepcopy(EditorProject(pkg).state().get("hand_store") or {})
    st["libraries"] = {**{k: "active" for k in old_libs}, **({new_lib: "archived"} if new_lib else {}),
                       PHASE2_LIBRARY: "active"}
    hs_old = engine_input(pkg, st, eng.sk, eng.layers, base_variants, eng.approvals, eng.source)
    hs_new = eng.hand_store
    poses = [("relaxed_standing", {}), ("running", {"phase": 0.25}), ("pointing_speaking", {}), ("hands_on_hips", {})]
    cols = [("original hand", None, {"r": "hand_r", "l": "hand_l"}), ("before: sleeve-attached library", hs_old, None),
            ("after: sleeve-free library", hs_new, None)]
    rows, recs = [], []
    for name, kw in poses:
        cells = []
        for title, hs, layers in cols:
            if hs is not None:
                eng.set_hand_store(hs, eng.approvals)
            r = eng.pose(name, allow_pending=True, visible=vis, hand_layers=layers, **kw)
            met = {s: hand_metrics(eng, r, s) for s in "rl"}
            img = Image.new("RGB", (2 * 190 + 6, 190), (255, 255, 255))
            img.paste(_close(r, "r", r=70, size=190), (0, 0))
            img.paste(_close(r, "l", r=70, size=190), (196, 0))
            sm = [(f"{s}: {met[s]['pose']} ({met[s]['cid'].split('/')[-1][:24]}) white "
                   f"{met[s]['seam'].get('own_white_px_near_wrist')} overhang {max(met[s]['seam'].get('overhang_px') or [0]):.0f}")
                  if met[s]["cid"] != "original" else f"{s}: the character's own hand (unchanged artwork)" for s in "rl"]
            cells.append(_caption(img, [f"{title}"] + sm, fs=11))
            recs.append({"pose": name, "column": title, "hands": {s: {k: met[s][k] for k in ("pose", "cid", "seam", "checks")}
                                                                   for s in "rl"}})
        eng.set_hand_store(hs_new, eng.approvals)
        rows.extend(cells)
    _grid(rows, 3, title="Before / after: original hand | sleeve-attached library (archived) | sleeve-free library "
                         "(same poses, auto selection, pending drawings previewed)").save(out / "before_after.jpg", quality=88)
    return {"file": "before_after.jpg", "cells": recs, "archived_libraries_restored_for_this_sheet": old_libs}


def ik_checks(eng, vis, geo: _Geo) -> dict[str, Any]:
    """The hand follows the solved wrist; switching drawings never moves a bone other than the hand (the wrist aim
    rotates the hand bone only) nor changes a bone length; FK->IK matching keeps the hand where it was."""
    name = "pointing_speaking"
    ref = eng.pose(name, allow_pending=True, skeleton_only=True, hand_aim=False)
    J0, T0 = ref["solve"]["joints_posed"], ref["solve"]["bone_tails_posed"]
    moved = {}
    for pose in ("relaxed", "fist", "pointing", "open_palm", "cupped"):
        for aim in (False, True):
            sw = {"hand_r": {"user": pose, "auto": False, "keys": [], "offsets": {}}}
            r = eng.pose(name, allow_pending=True, skeleton_only=True, switches=sw, hand_aim=aim)
            J, T = r["solve"]["joints_posed"], r["solve"]["bone_tails_posed"]
            d = max(float(np.hypot(J[b][0] - J0[b][0], J[b][1] - J0[b][1])) for b in J)
            dt = max(float(np.hypot(T[b][0] - T0[b][0], T[b][1] - T0[b][1])) for b in T if not b.startswith("hand_r"))
            lens = max(abs(float(np.hypot(T[b][0] - J[b][0], T[b][1] - J[b][1])) - eng.sk.bone(b).length) for b in J)
            moved[f"{pose}{'+aim' if aim else ''}"] = {"max_joint_move_px": round(d, 4), "max_tail_move_px_except_hand": round(dt, 4),
                                                         "max_bone_length_change_px": round(lens, 4)}
    fkik = {}
    for limb in ("arm_r", "arm_l"):
        m = eng.match_ik(limb, "ik", "relaxed_standing")
        fkik[limb] = {"jump_px": m["jump_px"], "max_rotation_jump_deg": m["max_rotation_jump_deg"]}
    # visible attachment relative to the cuff: rest vs IK poses (the hand rides on the wrist)
    att = {}
    for key, kw in (("thumbs_up_ik", {"ik": geo.thumbs("r"), "hand_actions": {"r": "thumbs_up"}}),
                    ("point_left_ik", {"ik": geo.point("l")[0], "hand_actions": {"l": "pointing"},
                                       "hand_targets": {"l": geo.point("l")[1]}})):
        s = "r" if key.startswith("thumbs") else "l"
        r = eng.pose("relaxed_standing", allow_pending=True, visible=vis, **kw)
        seam = posed_seam(r, eng, s, r["hands"][s]["layer"])
        att[key] = {"side": s, "pose": r["hands"][s]["pose"], "seam": seam,
                    "ik_status": (r["solve"].get("arms") or {}).get(s, {}).get("status")}
    ok = all(v["max_tail_move_px_except_hand"] < 1e-3 and v["max_bone_length_change_px"] < 0.02 for v in moved.values()) \
        and all(v["jump_px"] < 0.5 for v in fkik.values()) and all(a["seam"].get("covered") for a in att.values())
    return {"switching": moved, "fk_ik_match": fkik, "attachment_in_ik": att, "status": "OK" if ok else "CHECK"}


def garment_checks(eng, vis) -> dict[str, Any]:
    """Switching hands leaves the sleeve/forearm render unchanged (same pixels, same layer), the forearm cover is
    drawn over the hand (cuff overlap), and stored hands are rigid (no blend band, no stretch)."""
    name = "conversational_gesture"
    outs = {}
    for pose in ("relaxed", "fist", "open_palm"):
        sw = {"hand_r": {"user": pose, "auto": False, "keys": [], "offsets": {}}}
        outs[pose] = eng.pose(name, allow_pending=True, visible=vis, switches=sw)
    ref = outs["relaxed"]
    cover = next((c for c in ("forearm_r__hscuff", "forearm_r__trim", "forearm_r") if c in ref["render"]["alphas"]), None)
    same = {}
    for pose, r in outs.items():
        a0, a1 = ref["render"]["alphas"].get(cover), r["render"]["alphas"].get(cover)
        vis0 = (ref["render"]["top"] == ref["order"].index(cover)) if cover in ref["order"] else None
        vis1 = (r["render"]["top"] == r["order"].index(cover)) if cover in r["order"] else None
        same[pose] = {"cover": cover if cover in r["order"] else r["order"][r["order"].index(r["hands"]["r"]["layer"]) + 1]
                      if r["hands"]["r"]["layer"] in r["order"] else None,
                      "cover_alpha_identical": bool(a0 is not None and a1 is not None and np.array_equal(a0, a1)),
                      "hand_below_cover": r["order"].index(r["hands"]["r"]["layer"]) < max(
                          r["order"].index(c) for c in r["order"] if c.startswith("forearm_r")),
                      "upper_arm_pixels_identical": bool(np.array_equal(ref["render"]["alphas"].get("upper_arm_r"),
                                                                        r["render"]["alphas"].get("upper_arm_r")))}
        del vis0, vis1
    rig = {}
    for pose, r in outs.items():
        lay = r["hands"]["r"]["layer"]
        rig[pose] = (r["render"].get("stats") or {}).get(lay)
    from .handstore import classify  # noqa: PLC0415

    removed = {}
    for s in "rl":
        base = eng.variants.get(f"forearm_{s}__trim", eng.layers.get(f"forearm_{s}"))
        cov = eng.variants.get(f"forearm_{s}__hscuff")
        if base is None or cov is None:
            continue
        gone = (base[..., 3] > 0) & (cov[..., 3] == 0)
        c = classify(base)
        ref = next((v.placement["reference_wrist"] for vs in eng.hand_lib.candidates[s].values() for v in vs
                    if v.placement and v.placement.get("reference_wrist")), None)
        inside = np.ones(gone.shape, bool)
        if ref is not None:  # sleeve fabric ends at the white cuff's distal edge (+ its outline)
            H, W = gone.shape
            yy, xx = np.mgrid[0:H, 0:W]
            Wj, u = np.asarray(ref["wrist_joint"]), np.asarray(ref["axis"])
            inside = (xx - Wj[0]) * u[0] + (yy - Wj[1]) * u[1] <= ref["cuff_edge_t_px"] + 4.0
        removed[s] = {"removed_px": int(gone.sum()),
                      "sleeve_px_removed": int((gone & (c["navy"] | c["white"]) & inside).sum()),
                      "dark_px_beyond_cuff_removed": int((gone & (c["navy"] | c["white"]) & ~inside).sum()),
                      "skin_px_removed": int((gone & c["skin"]).sum())}
    store_rigid = all((st or {}).get("rigid") or ((st or {}).get("blended_fraction") == 0.0 and abs(st["det_min"] - 1) < 0.02
                                                    and abs(st["det_max"] - 1) < 0.02)
                      for p, st in rig.items() if "__hs." in (outs[p]["hands"]["r"]["layer"] or ""))
    ok = all(v["hand_below_cover"] and v["upper_arm_pixels_identical"] for v in same.values()) and store_rigid \
        and sum(v["sleeve_px_removed"] for v in removed.values()) == 0
    return {"pose": name, "switching": same, "rigidity": rig, "store_hands_rigid": store_rigid,
            "cover_vs_forearm": removed, "cover_removed_sleeve_px": sum(v["sleeve_px_removed"] for v in removed.values()),
            "rule": "forearm controls the sleeve, the hand bone controls the hand (rigid, no blend band)",
            "status": "OK" if ok else "CHECK"}


def override_checks(eng) -> dict[str, Any]:
    """Explicit user choices and switch keys win over the automatic choice; returning to auto restores it."""
    out = {}
    r = eng.pose("running", 0.25, allow_pending=True, skeleton_only=True)
    out["auto"] = {s: r["hands"][s]["pose"] for s in "rl"}
    sw = {"hand_r": {"user": "open_palm", "auto": False, "keys": [], "offsets": {}}}
    r = eng.pose("running", 0.25, allow_pending=True, skeleton_only=True, switches=sw)
    out["user_choice"] = {"requested": "open_palm", "shown": r["hands"]["r"]["pose"], "source": r["hands"]["r"]["source"],
                          "auto_would_be": r["hands"]["r"]["selection"]["chosen"]}
    sw = {"hand_r": {"user": None, "auto": True, "keys": [[0.0, "relaxed"]], "offsets": {}}}
    r = eng.pose("running", 0.25, allow_pending=True, skeleton_only=True, switches=sw)
    out["keyframe"] = {"key": "relaxed", "shown": r["hands"]["r"]["pose"], "source": r["hands"]["r"]["source"]}
    sw = {"hand_r": {"user": None, "auto": True, "keys": [], "offsets": {}}}
    r = eng.pose("running", 0.25, allow_pending=True, skeleton_only=True, switches=sw)
    out["reset_to_auto"] = {"shown": r["hands"]["r"]["pose"], "source": r["hands"]["r"]["source"]}
    r = eng.pose("running", 0.25, allow_pending=False, skeleton_only=True)
    out["approved_only"] = {s: {"pose": r["hands"][s]["pose"], "cid": r["hands"][s]["cid"]} for s in "rl"}
    ok = (out["user_choice"]["shown"] == "open_palm" and out["user_choice"]["source"] == "user"
          and out["keyframe"]["shown"] == "relaxed" and out["keyframe"]["source"] == "keyframe"
          and out["reset_to_auto"]["shown"] == out["auto"]["r"] and out["reset_to_auto"]["source"] == "auto"
          and all(v["cid"] == "original" for v in out["approved_only"].values()))
    out["status"] = "OK" if ok else "CHECK"
    return out


def persistence_check(pkg: Path, lib: str | None) -> dict[str, Any]:
    """Hand decisions survive save and reload (on a temporary copy of the package): library archive/restore,
    placement corrections (pivot, rotation offset, overlap), hand actions, switch choices; undo/redo."""
    from .editor.project import EditorProject

    tmp = Path(tempfile.mkdtemp(prefix="autorig_hr_"))
    try:
        dst = tmp / pkg.name
        shutil.copytree(pkg, dst, ignore=shutil.ignore_patterns("evidence*", "exports", "versions"))
        p = EditorProject(dst)
        uid = f"{lib}/right_pointing" if lib else None
        ops = [{"type": "handstore.library", "library": "schoolboy_hand_switches_v1", "status": "active"},
               {"type": "handstore.library", "library": "schoolboy_hand_switches_v1", "status": None},
               {"type": "handselect.action", "side": "l", "action": "thumbs_up", "target": None},
               {"type": "switch.set", "group": "hand_r", "variant": "fist", "auto": False}]
        if uid:
            ops.insert(2, {"type": "handstore.placement", "uid": uid,
                           "fields": {"pivot_x": 3.0, "pivot_y": -2.0, "rot": 4.0, "overlap": 2.0}})
        for op in ops:
            p.submit(op)
        st1 = p.state()
        q = EditorProject(dst)
        st2 = q.state()
        same = json.dumps({k: st1[k] for k in ("hand_store", "hand_selection", "switches")}, sort_keys=True) == \
            json.dumps({k: st2[k] for k in ("hand_store", "hand_selection", "switches")}, sort_keys=True)
        q.undo()
        undone = q.state()["switches"].get("hand_r", {}).get("user") is None
        q.redo()
        redone = q.state()["switches"].get("hand_r", {}).get("user") == "fist"
        return {"ops": len(ops), "reload_identical": same, "undo": undone, "redo": redone,
                "placement_saved": (st2["hand_store"]["placement"].get(uid) if uid else None),
                "status": "OK" if same and undone and redone else "CHECK"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def preservation_check(pkg: Path) -> dict[str, Any]:
    """Source artwork and every stored hand file are unchanged (hashes)."""
    import hashlib

    meta = json.loads((pkg / "metadata.json").read_text())
    src = meta["source"]
    f = pkg / src["path"] if src.get("path") else None
    src_ok = None
    if f is not None and f.is_file():
        src_ok = hashlib.sha256(f.read_bytes()).hexdigest() == src.get("sha256")
    bad = HandStore(pkg).verify()
    return {"source_sha256_matches": src_ok, "hand_store_files_modified": bad,
            "status": "OK" if not bad and src_ok is not False else "CHECK"}


def extraction_summary(pkg: Path, lib: str | None) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2] / "references" / "schoolboy" / "hand_pack_v2" / "extraction_report.json"
    if not root.is_file():
        return {"status": "UNCHECKED", "note": "extraction report not found"}
    r = json.loads(root.read_text())
    cells = r["cells"]
    clean = all(c["quality"]["outline_coverage"] >= 0.95 and c["quality"]["skin_on_edge_px"] <= 12
                and not c["touches_sheet_edge"] for c in cells.values())
    return {"drawings": len(cells), "clean_pixels": clean, "summary": r["summary"],
            "handedness": {n: c["side"] for n, c in r["handedness"]["per_cell"].items()},
            "status": "OK" if clean and len(cells) == 16 else "CHECK"}


def missing_report(eng) -> dict[str, Any]:
    lib = eng.hand_lib
    flagged = []
    for s in "rl":
        for pose, v in lib.hands[s].items():
            if v.available and (v.meta or {}).get("flags"):
                flagged.append({"side": s, "pose": pose, "cid": v.cid, "flags": v.meta["flags"]})
    pending = sorted({v.approval_item for s in "rl" for v in lib.hands[s].values()
                      if v.available and v.status == "pending_review" and v.approval_item})
    return {"missing": lib.missing(), "flagged": flagged, "pending_review": pending,
            "status": "OK" if lib.missing() or flagged or pending else "CHECK"}


def gates(r: dict[str, Any], eng, lib: str | None) -> list[dict[str, Any]]:
    tests = r["tests"]
    static = [t for t in tests if "hands" in t]
    act = {s: eng.hand_lib.hands[s] for s in "rl"}
    old_active = [f"{s}/{p}: {v.cid}" for s in "rl" for p, v in act[s].items()
                  if v.available and (v.cid.startswith("phase2:") or (v.meta or {}).get("sleeve") == "attached")]
    sides_ok = all(sum(1 for p, v in act[s].items() if v.available and v.cid.startswith(f"store:{lib}/")) >= 7 for s in "rl")
    attach = all(all(t["hands"][s]["checks"].get(k, True) for s in "rl" for k in (  # n/a for the original hand
        "attachment", "no_large_gap", "no_excessive_overlap", "no_sleeve_duplication")) for t in static) \
        and all(d["rest_checks"]["attachment"] for d in r["library_sheet"]["drawings"])
    gesture = all(t["hands"][s]["checks"]["gesture"] and t["hands"][s]["checks"]["handedness"] for t in static for s in "rl")
    seq = next(t for t in tests if t["id"].startswith("10_"))
    ba = r["before_after"]["cells"]

    def white(col):
        return sum((c["hands"][s]["seam"].get("own_white_px_near_wrist") or 0) for c in ba if c["column"].startswith(col) for s in "rl")

    def overhang(col):
        return max(max(c["hands"][s]["seam"].get("overhang_px") or [0]) for c in ba if c["column"].startswith(col) for s in "rl")

    improved = white("after") < white("before") and overhang("after") <= overhang("before")
    G = [
        ("G1", "All 16 hand assets are extracted correctly", r["extraction"]["status"] == "OK",
         "extraction_sheet.jpg (references/schoolboy/hand_pack_v2), library_attached.jpg"),
        ("G2", "Both hands have functioning switch groups", sides_ok and r["overrides"]["status"] == "OK", "results.json overrides"),
        ("G3", "The old sleeve-attached variants are no longer active", not old_active, f"active old: {old_active or 'none'}"),
        ("G4", "All new hands attach naturally at the wrist", attach, "tests/*.png, library_attached.jpg (visual review pending)"),
        ("G5", "The forearm remains unchanged during hand switching", r["ik"]["status"] == "OK" and all(
            v["upper_arm_pixels_identical"] and (v["cover_alpha_identical"] or v["cover"] != "forearm_r__hscuff")
            for v in r["garments"]["switching"].values()) and r["garments"]["cover_removed_sleeve_px"] == 0,
         "ik.switching, garments.switching (stored drawings share one cover; the cover differs from the original "
         "forearm layer only by old-hand pixels)"),
        ("G6", "Automatic hand selection produces sensible results", gesture, "auto_selection.jpg, tests/*.png"),
        ("G7", "Manual selection overrides automatic behaviour", r["overrides"]["status"] == "OK", "results.json overrides"),
        ("G8", "Hand switching does not create excessive popping", seq["status"] == "OK", "switch_sequence.gif/png"),
        ("G9", "Existing IK continues working", r["ik"]["status"] == "OK", "ik.fk_ik_match, tests 05/07/08"),
        ("G10", "Garment deformation remains unaffected", r["garments"]["status"] == "OK" and all(
            t["preserved"].get("clothing_or_face_px_changed") == 0 for t in static), "garments, tests preserved"),
        ("G11", "Projects save and reload correctly", r["persistence"]["status"] == "OK", "persistence"),
        ("G12", "Visual comparisons demonstrate improvement", improved, "before_after.jpg (judged by measurements; "
                                                                        "visual judgement is the user's)"),
        ("G13", "Missing or unsuitable artwork is reported", r["missing_and_unsuitable"]["status"] == "OK", "missing_and_unsuitable"),
        ("G14", "Existing character source artwork is preserved", r["preservation"]["status"] == "OK", "preservation"),
    ]
    return [{"id": g, "gate": t, "status": "PASS" if ok else "FAIL", "evidence": e} for g, t, ok, e in G]
