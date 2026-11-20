"""Synthetic ground-truth cartoon character (test fixture, NOT a reference).

A procedurally drawn, front-facing, full-body cartoon schoolboy-style figure
with dark linework, flat fills, an oversized head and a closed mouth, on a
NON-square canvas. Because every pixel comes from a known primitive, exact
ground truth is available for:

* visible anatomical part masks (hair, head, neck, torso, pelvis, upper arms,
  forearms, hands, thighs, shins, feet),
* clothing masks (shirt, shorts, shoes),
* facial feature masks (eyes, irises, pupils, eyebrows, nose, mouth, ears),
* skeletal joint positions and facial landmarks.

It exists to test the Phase 1 pipeline and to score real SAM 3 output
against known truth. It must never be presented as the user's reference
character: the real schoolboy reference is a separate input.

Side convention: the character faces the viewer, so the character's LEFT
(suffix ``_l``) appears on the IMAGE RIGHT (larger x).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

W, H = 1000, 1500
SS = 2  # supersampling
OUTLINE_W = 5.0

BG = (236, 240, 246)
OUTLINE = (35, 30, 45)
SKIN = (247, 210, 175)
SKIN_SHADE = (232, 186, 150)
HAIR = (92, 56, 30)
SHIRT = (172, 202, 236)
COLLAR = (250, 250, 252)
TIE = (200, 44, 54)
SHORTS = (46, 60, 112)
SOCK = (250, 250, 250)
SHOE = (42, 36, 38)
EYE_WHITE = (255, 255, 255)
IRIS = (70, 122, 200)
PUPIL = (20, 20, 30)
BROW = (72, 44, 24)
LIP = (150, 60, 60)

# --- ground-truth joints (pixels) ---------------------------------------------
JOINTS: dict[str, tuple[float, float]] = {
    "head": (500, 330), "neck": (500, 540), "torso": (500, 720), "pelvis": (500, 990),  # pelvis = hip centre
    "shoulder_r": (385, 600), "shoulder_l": (615, 600),
    "elbow_r": (330, 770), "elbow_l": (670, 770),
    "wrist_r": (302, 905), "wrist_l": (698, 905),
    "hand_r": (292, 952), "hand_l": (708, 952),
    "hip_r": (445, 990), "hip_l": (555, 990),
    "knee_r": (440, 1170), "knee_l": (560, 1170),
    "ankle_r": (440, 1340), "ankle_l": (560, 1340),
    "foot_r": (425, 1385), "foot_l": (575, 1385),
}

HEAD_C, HEAD_RX, HEAD_RY = (500.0, 330.0), 205.0, 215.0
EYE_R_C, EYE_L_C = (425.0, 335.0), (575.0, 335.0)
IRIS_OFF, IRIS_R, PUPIL_R = 7.0, 27.0, 13.0
MOUTH_BOX = (440.0, 420.0, 560.0, 485.0)


@dataclass(frozen=True)
class Prim:
    kind: str  # ellipse | capsule | rrect | polygon | arc | chord | rect
    geom: tuple
    fill: tuple[int, int, int] | None
    outlined: bool = True
    width: float = 0.0  # stroke width for arcs


@dataclass(frozen=True)
class Layer:
    name: str
    part: str  # anatomical part this layer's pixels belong to
    prims: tuple[Prim, ...]
    clothing: str | None = None
    face: str | None = None  # facial feature id, if any
    details: tuple[Prim, ...] = ()  # drawn on top without outline pass


def _s(v):
    return v * SS


def _draw(draw: ImageDraw.ImageDraw, p: Prim, grow: float, color) -> None:
    g = p.geom
    if p.kind == "ellipse":
        (cx, cy), rx, ry = g
        draw.ellipse((_s(cx - rx - grow), _s(cy - ry - grow), _s(cx + rx + grow), _s(cy + ry + grow)), fill=color)
    elif p.kind == "capsule":
        (x0, y0), (x1, y1), w = g
        half = w / 2 + grow
        draw.line(((_s(x0), _s(y0)), (_s(x1), _s(y1))), fill=color, width=max(1, round(_s(2 * half))))
        for cx, cy in ((x0, y0), (x1, y1)):
            draw.ellipse((_s(cx - half), _s(cy - half), _s(cx + half), _s(cy + half)), fill=color)
    elif p.kind in ("rrect", "rect"):
        x0, y0, x1, y1 = g[:4]
        r = g[4] if p.kind == "rrect" else 0
        box = (_s(x0 - grow), _s(y0 - grow), _s(x1 + grow), _s(y1 + grow))
        if r:
            draw.rounded_rectangle(box, radius=_s(r + grow), fill=color)
        else:
            draw.rectangle(box, fill=color)
    elif p.kind == "polygon":
        pts = g
        if grow:
            cx = sum(x for x, _ in pts) / len(pts)
            cy = sum(y for _, y in pts) / len(pts)
            pts = [(x + grow * np.sign(x - cx), y + grow * np.sign(y - cy)) for x, y in pts]
        draw.polygon([(_s(x), _s(y)) for x, y in pts], fill=color)
    elif p.kind == "chord":
        box, a0, a1 = g
        box = (box[0] - grow, box[1] - grow, box[2] + grow, box[3] + grow)
        draw.chord(tuple(_s(v) for v in box), a0, a1, fill=color)
    elif p.kind == "arc":
        box, a0, a1 = g
        draw.arc(tuple(_s(v) for v in box), a0, a1, fill=color, width=max(1, round(_s(p.width))))
    else:  # pragma: no cover
        raise ValueError(p.kind)


def _render_layer(layer: Layer) -> Image.Image:
    big = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    for p in layer.prims:
        if p.outlined:
            _draw(d, p, OUTLINE_W, OUTLINE + (255,))
    for p in layer.prims:
        _draw(d, p, 0.0, (p.fill or OUTLINE) + (255,))
    for p in layer.details:
        _draw(d, p, 0.0, (p.fill or OUTLINE) + (255,))
    return big.resize((W, H), Image.Resampling.LANCZOS)


def _limb(name, part, a, b, w, color, clothing=None, details=()):
    return Layer(name, part, (Prim("capsule", (JOINTS[a], JOINTS[b], w), color),), clothing=clothing, details=details)


def _eye(side: str, c: tuple[float, float]) -> list[Layer]:
    sign = 1 if side == "r" else -1
    iris_c = (c[0] + sign * IRIS_OFF, c[1] + 7)
    return [
        Layer(f"eye_white_{side}", "head", (Prim("ellipse", (c, 46, 56), EYE_WHITE),), face=f"eye_{side}"),
        Layer(f"iris_{side}", "head", (Prim("ellipse", (iris_c, IRIS_R, IRIS_R), IRIS, outlined=False),),
              face=f"iris_{side}"),
        Layer(f"pupil_{side}", "head", (Prim("ellipse", (iris_c, PUPIL_R, PUPIL_R), PUPIL, outlined=False),),
              face=f"pupil_{side}",
              details=(Prim("ellipse", ((iris_c[0] + 9, iris_c[1] - 9), 6, 6), EYE_WHITE, outlined=False),)),
    ]


def build_layers() -> list[Layer]:
    """Back-to-front drawing order."""
    sock = lambda s: (Prim("rect", (JOINTS[f"ankle_{s}"][0] - 35, 1295, JOINTS[f"ankle_{s}"][0] + 35, 1345), SOCK,
                           outlined=False),)
    layers: list[Layer] = []
    for s in ("r", "l"):
        layers += [
            _limb(f"thigh_{s}", f"thigh_{s}", f"hip_{s}", f"knee_{s}", 82, SKIN),
            _limb(f"shin_{s}", f"shin_{s}", f"knee_{s}", f"ankle_{s}", 74, SKIN, details=sock(s)),
            Layer(f"shoe_{s}", f"foot_{s}", (Prim("ellipse", (JOINTS[f"foot_{s}"], 62, 34), SHOE),), clothing="shoes"),
        ]
    layers.append(Layer("shorts", "pelvis", (Prim("rrect", (372, 880, 628, 1065, 28), SHORTS),), clothing="shorts"))
    layers.append(Layer("neck", "neck", (Prim("rect", (470, 500, 530, 600), SKIN_SHADE),)))
    layers.append(Layer(
        "shirt_body", "torso", (Prim("rrect", (370, 565, 630, 905, 50), SHIRT),), clothing="shirt",
        details=(Prim("polygon", ((470, 575), (500, 615), (530, 575)), COLLAR, outlined=False),
                 Prim("polygon", ((492, 612), (508, 612), (514, 760), (500, 785), (486, 760)), TIE, outlined=False))))
    for s in ("r", "l"):
        sh, el = JOINTS[f"shoulder_{s}"], JOINTS[f"elbow_{s}"]
        sleeve_end = (sh[0] + 0.62 * (el[0] - sh[0]), sh[1] + 0.62 * (el[1] - sh[1]))
        layers += [
            _limb(f"upper_arm_skin_{s}", f"upper_arm_{s}", f"shoulder_{s}", f"elbow_{s}", 66, SKIN),
            Layer(f"sleeve_{s}", f"upper_arm_{s}", (Prim("capsule", (sh, sleeve_end, 94), SHIRT),), clothing="shirt"),
            _limb(f"forearm_{s}", f"forearm_{s}", f"elbow_{s}", f"wrist_{s}", 60, SKIN),
            Layer(f"hand_{s}", f"hand_{s}", (Prim("ellipse", (JOINTS[f"hand_{s}"], 42, 46), SKIN),)),
        ]
    for s, x in (("r", 292.0), ("l", 708.0)):
        layers.append(Layer(f"ear_{s}", "head", (Prim("ellipse", ((x, 345.0), 42, 48), SKIN),), face=f"ear_{s}"))
    layers.append(Layer("head_skin", "head", (Prim("ellipse", (HEAD_C, HEAD_RX, HEAD_RY), SKIN),), face="head_base"))
    layers.append(Layer("nose", "head", (Prim("ellipse", ((500.0, 405.0), 15, 11), SKIN_SHADE),), face="nose"))
    layers.append(Layer("mouth", "head", (), face="mouth",
                        details=(Prim("arc", (MOUTH_BOX, 25, 155), LIP, outlined=False, width=8),)))
    layers += _eye("r", EYE_R_C) + _eye("l", EYE_L_C)
    layers.append(Layer("brow_r", "head", (Prim("capsule", ((372, 268), (462, 258), 16), BROW, outlined=False),),
                        face="brow_r"))
    layers.append(Layer("brow_l", "head", (Prim("capsule", ((538, 258), (628, 268), 16), BROW, outlined=False),),
                        face="brow_l"))
    hb = (HEAD_C[0] - HEAD_RX, HEAD_C[1] - HEAD_RY, HEAD_C[0] + HEAD_RX, HEAD_C[1] + HEAD_RY)
    layers.append(Layer("hair", "hair", (
        Prim("chord", (hb, 205, 335), HAIR),
        Prim("polygon", ((330, 215), (400, 195), (470, 225), (520, 190), (600, 222), (668, 205), (690, 240),
                         (640, 232), (580, 246), (520, 232), (460, 248), (400, 232), (340, 246)), HAIR),
    ), face="hair"))
    return layers


@dataclass
class SyntheticCharacter:
    image: Image.Image  # RGB, opaque background
    layers: list[Layer]
    layer_rgba: dict[str, np.ndarray]
    visible: dict[str, np.ndarray]  # layer name -> visible bool mask
    anatomical: dict[str, np.ndarray] = field(default_factory=dict)
    clothing: dict[str, np.ndarray] = field(default_factory=dict)
    face: dict[str, np.ndarray] = field(default_factory=dict)
    silhouette: np.ndarray | None = None
    joints: dict[str, tuple[float, float]] = field(default_factory=lambda: dict(JOINTS))
    landmarks: dict[str, tuple[float, float]] = field(default_factory=dict)

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size


def _arc_point(box, deg):
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return (cx + (box[2] - box[0]) / 2 * math.cos(math.radians(deg)),
            cy + (box[3] - box[1]) / 2 * math.sin(math.radians(deg)))


def render() -> SyntheticCharacter:
    layers = build_layers()
    canvas = Image.new("RGBA", (W, H), BG + (255,))
    rgba: dict[str, np.ndarray] = {}
    for layer in layers:
        img = _render_layer(layer)
        canvas.alpha_composite(img)
        rgba[layer.name] = np.asarray(img)
    covered = np.zeros((H, W), dtype=bool)
    visible: dict[str, np.ndarray] = {}
    for layer in reversed(layers):  # front to back
        own = rgba[layer.name][..., 3] >= 128
        visible[layer.name] = own & ~covered
        covered |= own
    ch = SyntheticCharacter(image=canvas.convert("RGB"), layers=layers, layer_rgba=rgba, visible=visible,
                            silhouette=covered)
    hip_y = JOINTS["hip_r"][1]
    for layer in layers:
        v = visible[layer.name]
        part = layer.part
        if layer.name == "shorts":  # anatomical boundary cuts through clothing at the hip line
            ys = np.arange(H)[:, None]
            xs = np.arange(W)[None, :]
            below = v & (ys >= hip_y)
            ch.anatomical["pelvis"] = ch.anatomical.get("pelvis", np.zeros_like(v)) | (v & (ys < hip_y))
            ch.anatomical["thigh_r"] = ch.anatomical.get("thigh_r", np.zeros_like(v)) | (below & (xs < 500))
            ch.anatomical["thigh_l"] = ch.anatomical.get("thigh_l", np.zeros_like(v)) | (below & (xs >= 500))
        else:
            ch.anatomical[part] = ch.anatomical.get(part, np.zeros_like(v)) | v
        if layer.clothing:
            ch.clothing[layer.clothing] = ch.clothing.get(layer.clothing, np.zeros_like(v)) | v
        if layer.face:
            ch.face[layer.face] = ch.face.get(layer.face, np.zeros_like(v)) | v
    # eye masks include iris + pupil; iris includes pupil (as drawn objects)
    for s in ("r", "l"):
        ch.face[f"iris_{s}"] = ch.face[f"iris_{s}"] | ch.face[f"pupil_{s}"]
        ch.face[f"eye_{s}"] = ch.face[f"eye_{s}"] | ch.face[f"iris_{s}"]
    # The mouth is pure linework: its GT mask is the stroke itself.
    sign = {"r": 1, "l": -1}
    a, b = _arc_point(MOUTH_BOX, 155), _arc_point(MOUTH_BOX, 25)
    ch.landmarks = {
        "eye_center_r": EYE_R_C, "eye_center_l": EYE_L_C,
        "pupil_center_r": (EYE_R_C[0] + sign["r"] * IRIS_OFF, EYE_R_C[1] + 7),
        "pupil_center_l": (EYE_L_C[0] + sign["l"] * IRIS_OFF, EYE_L_C[1] + 7),
        "brow_center_r": (417.0, 263.0), "brow_center_l": (583.0, 263.0),
        "mouth_corner_r": a, "mouth_corner_l": b,
        "mouth_center": ((MOUTH_BOX[0] + MOUTH_BOX[2]) / 2, MOUTH_BOX[3]),
        "nose_tip": (500.0, 405.0), "chin": (500.0, HEAD_C[1] + HEAD_RY),
    }
    for s_ in ("r", "l"):  # visible ear (half hidden behind the head): centroid of what is visible
        ys, xs = np.nonzero(ch.face[f"ear_{s_}"])
        ch.landmarks[f"ear_center_{s_}"] = (float(xs.mean()), float(ys.mean()))
    return ch


# --- canned "segmentation" answers for the offline fixture provider ------------------

def concept_masks(ch: SyntheticCharacter) -> dict[str, list[np.ndarray]]:
    """What an ideal concept segmenter would return per text prompt
    (instances ordered image-left to image-right)."""
    A, C, F = ch.anatomical, ch.clothing, ch.face
    head_all = A["head"]
    V = ch.visible
    # Concept answers follow VISIBLE artwork, not anatomy: "arm"/"leg" are the
    # bare-skin limbs (sleeves and shorts belong to "shirt"/"shorts").
    arm = lambda s: V[f"upper_arm_skin_{s}"] | V[f"forearm_{s}"]
    leg = lambda s: V[f"thigh_{s}"] | V[f"shin_{s}"]
    face_region = head_all & ~(F["ear_r"] | F["ear_l"])
    return {
        "person": [ch.silhouette.copy()],
        "hair": [A["hair"]],
        "head": [head_all | A["hair"]],
        "face": [face_region],
        "neck": [A["neck"]],
        "shirt": [C["shirt"]],
        "sleeve": [V["sleeve_r"], V["sleeve_l"]],
        "arm": [arm("r"), arm("l")],
        "hand": [A["hand_r"], A["hand_l"]],
        "shorts": [C["shorts"]],
        "leg": [leg("r"), leg("l")],
        "shoe": [A["foot_r"], A["foot_l"]],
        "eye": [F["eye_r"], F["eye_l"]],
        "eyebrow": [F["brow_r"], F["brow_l"]],
        "pupil": [F["pupil_r"], F["pupil_l"]],
        "iris": [F["iris_r"], F["iris_l"]],
        "nose": [F["nose"]],
        "mouth": [F["mouth"]],
        "ear": [F["ear_r"], F["ear_l"]],
    }
