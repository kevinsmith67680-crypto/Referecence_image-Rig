"""Python simulation of the rig: render posed frames without Moho.

Each art layer is moved rigidly with its bone (what Moho's layer binding
does) using the same forward kinematics that produced the Lua numbers. The
output is a *simulation* for reviewing joints, pivots and the animation in
the cloud. It is not a Moho render and proves nothing about Moho itself.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .geometry import (
    BonePose,
    compute_rest_pose,
    layer_delta_px,
    pose_at,
    world_to_px,
)
from .spec import RigSpec

BACKGROUND = (246, 244, 238, 255)
BONE_COLOR = (220, 40, 90, 255)
JOINT_COLOR = (255, 255, 255, 255)


def _invert_affine_px(m) -> tuple[float, ...]:
    """PIL's AFFINE transform wants output->input coefficients (a,b,c,d,e,f)."""
    a, b, c = m[0]
    d, e, f = m[1]
    det = a * e - b * d
    ia, ib = e / det, -b / det
    id_, ie = -d / det, a / det
    ic = -(ia * c + ib * f)
    if_ = -(id_ * c + ie * f)
    return (ia, ib, ic, id_, ie, if_)


def load_layers(spec: RigSpec, asset_root: str | Path) -> dict[str, Image.Image]:
    root = Path(asset_root)
    return {p.name: Image.open(root / p.image).convert("RGBA") for p in spec.parts}


def render_frame(
    spec: RigSpec,
    layers: dict[str, Image.Image],
    frame: float,
    draw_bones: bool = False,
    background: tuple[int, int, int, int] | None = BACKGROUND,
) -> Image.Image:
    w, h = spec.canvas_width, spec.canvas_height
    rest = compute_rest_pose(spec)
    rest_poses = {r.name: BonePose(r.name, r.world_base, r.world_angle, r.length) for r in rest}
    poses = pose_at(spec, rest, frame)
    canvas = Image.new("RGBA", (w, h), background or (0, 0, 0, 0))
    for part in spec.parts_back_to_front():
        m = layer_delta_px(w, h, rest_poses[part.bone], poses[part.bone])
        moved = layers[part.name].transform(
            (w, h), Image.Transform.AFFINE, _invert_affine_px(m), resample=Image.Resampling.BICUBIC
        )
        canvas.alpha_composite(moved)
    if draw_bones:
        draw = ImageDraw.Draw(canvas)
        for pose in poses.values():
            b = world_to_px(pose.world_base, w, h)
            t = world_to_px(pose.world_tip, w, h)
            draw.line((b, t), fill=BONE_COLOR, width=5)
            draw.ellipse((b[0] - 9, b[1] - 9, b[0] + 9, b[1] + 9), fill=JOINT_COLOR, outline=BONE_COLOR, width=3)
    return canvas


def _crop_box(spec: RigSpec) -> tuple[int, int, int, int]:
    # Region around the character, generous enough for the full arm wave.
    xs = [v for b in spec.bones for v in (b.base_px[0], b.tip_px[0])]
    ys = [v for b in spec.bones for v in (b.base_px[1], b.tip_px[1])]
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    half_w, half_h = 600, 400
    x0 = int(max(0, min(spec.canvas_width - 2 * half_w, cx - half_w)))
    y0 = int(max(0, min(spec.canvas_height - 2 * half_h, cy - half_h)))
    return (x0, y0, x0 + 2 * half_w, y0 + 2 * half_h)


def render_contact_sheet(spec: RigSpec, asset_root: str | Path, out_path: str | Path,
                         frames: list[int] | None = None) -> Path:
    layers = load_layers(spec, asset_root)
    if frames is None:
        key_frames = sorted({k.frame for t in spec.tracks for k in t.keys})
        frames = [spec.rest_frame] + key_frames
    box = _crop_box(spec)
    cell_w, cell_h = (box[2] - box[0]) // 2, (box[3] - box[1]) // 2
    cols = 3
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGBA", (cols * cell_w, rows * cell_h + 28), (255, 255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    for i, f in enumerate(frames):
        img = render_frame(spec, layers, f, draw_bones=True).crop(box).resize((cell_w, cell_h), Image.Resampling.LANCZOS)
        x, y = (i % cols) * cell_w, (i // cols) * cell_h
        sheet.paste(img, (x, y))
        label = "frame 0 (rest/setup)" if f == spec.rest_frame else f"frame {f}"
        draw.rectangle((x, y, x + cell_w - 1, y + cell_h - 1), outline=(200, 200, 200, 255))
        draw.text((x + 10, y + 8), label, fill=(20, 20, 20, 255))
    draw.text((10, rows * cell_h + 8),
              "PYTHON SIMULATION of layer binding + FK (not a Moho render). Pink = bones, white dots = pivots.",
              fill=(120, 0, 40, 255))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(out_path, optimize=True)
    return out_path


def render_gif(spec: RigSpec, asset_root: str | Path, out_path: str | Path, fps: int = 24,
               scale: float = 0.5) -> Path:
    layers = load_layers(spec, asset_root)
    box = _crop_box(spec)
    size = (int((box[2] - box[0]) * scale), int((box[3] - box[1]) * scale))
    frames = []
    for f in range(spec.start_frame, spec.end_frame + 1):
        img = render_frame(spec, layers, f).crop(box).resize(size, Image.Resampling.LANCZOS).convert("RGB")
        frames.append(img.quantize(colors=96, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(out_path, save_all=True, append_images=frames[1:], duration=round(1000 / fps), loop=0,
                   optimize=False, disposal=1)
    return out_path
