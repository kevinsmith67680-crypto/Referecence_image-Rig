"""Phase 3.7 hand asset audit (``autorig hand-audit <package> --inspection <semantic_inspection.json>``).

Builds the HandAssetMetadata inventory for every hand candidate of a package:
- the original reference hands;
- the active store drawings;
- the Phase 2 sheet hands, when active.

It writes ``<package>/analysis/hand_asset_metadata.json`` and the evidence in
``<package>/natural_pose/hand_selection/inventory/``:
- ``inventory.json``;
- ``inventory_sheet.png``: each drawing as drawn, then placed at rest on its stored side and on its corrected side.

The audit measures the stored assignment: it builds the engine without the metadata, so a drawing that was stored for
the wrong side shows the large rest rotation that exposes it. Nothing is approved and no stored file is modified.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw

from . import handmeta
from .handmeta import HandOrientationAnalyzer

OUT = "natural_pose/hand_selection/inventory"


def _rgba_on(bg: tuple[int, int, int], img: np.ndarray) -> Image.Image:
    im = Image.fromarray(img).convert("RGBA")
    b = Image.new("RGBA", im.size, bg + (255,))
    b.alpha_composite(im)
    return b.convert("RGB")


def _crop_alpha(img: np.ndarray, pad: int = 6) -> np.ndarray:
    ys, xs = np.nonzero(img[..., 3] > 0)
    if not len(xs):
        return img[:1, :1]
    return img[max(0, ys.min() - pad):ys.max() + pad, max(0, xs.min() - pad):xs.max() + pad]


def _fit(im: Image.Image, w: int, h: int) -> Image.Image:
    s = min(w / max(im.width, 1), h / max(im.height, 1), 2.0)
    return im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))))


def hand_audit(pkg: str | Path, inspection: str | Path | None, progress: Callable[[str], Any] = print) -> dict[str, Any]:
    from .handstore import HandStore
    from .motion.pipeline import engine_for_package

    pkg = Path(pkg)
    insp = handmeta.load_inspection(inspection)
    if inspection is not None and not insp:
        raise FileNotFoundError(f"inspection record not found: {inspection}")
    eng0, _ = engine_for_package(pkg, use_hand_metadata=False)
    store = HandStore(pkg) if (pkg / "hand_store" / "index.json").is_file() else None
    inv = handmeta.build_inventory(eng0, store, insp)
    handmeta.save(pkg, inv)
    progress(f"metadata: {len(inv['assets'])} drawings -> {handmeta.METADATA_FILE}")
    # the engine as it will be used (metadata applied): corrected placements
    eng1, _ = engine_for_package(pkg)
    out = pkg / OUT
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    an = HandOrientationAnalyzer()
    for key, rec in sorted(inv["assets"].items(), key=lambda kv: (kv[1]["store_side"], kv[1]["label_pose"])):
        if rec["cid"] == "original":
            continue
        uid = rec.get("uid")
        tiles = []
        if store is not None and uid:
            spr = store.image(uid, rec.get("version"))
            tiles.append(("as drawn", _rgba_on((235, 235, 235), spr)))
        for label, eng, side in (("stored side, at rest", eng0, rec["store_side"]),
                                 ("offered side, at rest", eng1, rec["use_side"])):
            v = next((c for c in eng.hand_lib.candidates[side].get(rec["label_pose"], []) if c.cid == rec["cid"]), None)
            if v is None or v.layer is None:
                continue
            img = {**eng.layers, **eng.variants}.get(v.layer)
            if img is None:
                continue
            fa = eng.variants.get(f"forearm_{side}__hscuff", eng.layers.get(f"forearm_{side}"))
            comp = np.zeros_like(img)
            if fa is not None:
                comp = fa.copy()
            a = img[..., 3:4].astype(np.float32) / 255.0
            comp = (img.astype(np.float32) * a + comp.astype(np.float32) * (1 - a)).astype(np.uint8)
            comp[..., 3] = np.maximum(img[..., 3], comp[..., 3])
            ys, xs = np.nonzero(img[..., 3] > 0)
            cx, cy = int(xs.mean()), int(ys.mean())
            box = comp[max(0, cy - 170):cy + 170, max(0, cx - 200):cx + 200]
            ang = ((v.placement or {}).get("final") or {}).get("angle_deg")
            pa = an.analyze(rec, ang or 0.0)
            tiles.append((f"{label} ({'R' if side == 'r' else 'L'}): rot {ang:.0f}, {pa['palm_class']}"
                          if ang is not None else label, _rgba_on((255, 255, 255), _crop_alpha(box))))
        rows.append((rec, tiles))
    W, TH = 1500, 230
    sheet = Image.new("RGB", (W, 40 + len(rows) * (TH + 36)), "white")
    d = ImageDraw.Draw(sheet)
    d.text((10, 10), "Phase 3.7 hand asset audit: each drawing as drawn, placed at rest on the side it was stored for, "
                     "and on the side its inspected handedness gives (all drawings PENDING_REVIEW)", fill=(0, 0, 0))
    y = 40
    for rec, tiles in rows:
        col = (170, 0, 0) if rec["flags"] and (rec["reassigned"] or not rec["label_agrees"]) else (0, 0, 0)
        d.text((10, y), f"{rec['key']}  label={rec['label_pose']}  shows={rec.get('gesture')}  "
                        f"hand={rec.get('handedness')} ({rec.get('handedness_confidence')})  "
                        f"surface={rec.get('visible_surface')}  tol={rec.get('rotation_tolerance_deg')}  "
                        f"{rec['approval']['state']}", fill=col)
        if rec["flags"]:
            d.text((10, y + 14), "; ".join(rec["flags"])[:230], fill=(170, 0, 0))
        x = 10
        for label, im in tiles:
            t = _fit(im, 430, TH - 24)
            sheet.paste(t, (x, y + 32))
            d.text((x, y + 32 + t.height + 1), label, fill=(60, 60, 60))
            x += 470
        y += TH + 36
    sheet.save(out / "inventory_sheet.png")
    summ = handmeta.summary(inv)
    (out / "inventory.json").write_text(json.dumps({"summary": summ, "assets": inv["assets"]}, indent=1, default=float)
                                        + "\n", encoding="utf-8")
    progress(f"reassigned: {summ['reassigned']}; label disagrees: {summ['label_disagrees']}")
    return {"summary": summ, "metadata": str(pkg / handmeta.METADATA_FILE), "sheet": str(out / "inventory_sheet.png")}


__all__ = ["hand_audit"]
