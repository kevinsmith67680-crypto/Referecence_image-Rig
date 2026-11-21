// Pure geometry helpers (unit-tested). Image space = source pixels, origin top-left, +y down.
// The canvas only applies a view transform: screen = image * scale + offset.

export interface View { scale: number; x: number; y: number }
export interface Pt { x: number; y: number }
export interface VNode { x: number; y: number; hin?: [number, number]; hout?: [number, number] }

export const MIN_SCALE = 0.05;
export const MAX_SCALE = 32;

export function screenToImage(v: View, p: Pt): Pt {
  return { x: (p.x - v.x) / v.scale, y: (p.y - v.y) / v.scale };
}

export function imageToScreen(v: View, p: Pt): Pt {
  return { x: p.x * v.scale + v.x, y: p.y * v.scale + v.y };
}

/** Zoom by `factor` keeping the image point under `screen` fixed. */
export function zoomAt(v: View, screen: Pt, factor: number): View {
  const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, v.scale * factor));
  const ip = screenToImage(v, screen);
  return { scale, x: screen.x - ip.x * scale, y: screen.y - ip.y * scale };
}

export function fitView(imgW: number, imgH: number, boxW: number, boxH: number, margin = 0.94): View {
  const scale = Math.min(boxW / imgW, boxH / imgH) * margin;
  return { scale, x: (boxW - imgW * scale) / 2, y: (boxH - imgH * scale) / 2 };
}

/** Focus a rectangle (image px) in the viewport. */
export function focusRect(r: [number, number, number, number], boxW: number, boxH: number, margin = 0.8): View {
  const w = Math.max(1, r[2] - r[0]);
  const h = Math.max(1, r[3] - r[1]);
  const scale = Math.min(MAX_SCALE, Math.min(boxW / w, boxH / h) * margin);
  return { scale, x: boxW / 2 - ((r[0] + r[2]) / 2) * scale, y: boxH / 2 - ((r[1] + r[3]) / 2) * scale };
}

/** Same rule as the backend: a soft brush's 50% contour keeps masks binary (layer partition). */
export function effectiveBrushRadius(radius: number, hardness: number): number {
  const h = Math.min(1, Math.max(0, hardness));
  return Math.max(0.5, radius * (0.5 + 0.5 * h));
}

/** SVG path data for a cubic Bezier vector (matches autorig.editor.vectors.path_d). */
export function pathD(nodes: VNode[], closed: boolean): string {
  if (!nodes.length) return "";
  const f = (n: number) => n.toFixed(2);
  const parts = [`M ${f(nodes[0].x)} ${f(nodes[0].y)}`];
  const segs: [VNode, VNode][] = [];
  for (let i = 0; i < nodes.length - 1; i++) segs.push([nodes[i], nodes[i + 1]]);
  if (closed && nodes.length > 1) segs.push([nodes[nodes.length - 1], nodes[0]]);
  for (const [a, b] of segs) {
    const ho = a.hout ?? [0, 0];
    const hi = b.hin ?? [0, 0];
    parts.push(`C ${f(a.x + ho[0])} ${f(a.y + ho[1])} ${f(b.x + hi[0])} ${f(b.y + hi[1])} ${f(b.x)} ${f(b.y)}`);
  }
  if (closed) parts.push("Z");
  return parts.join(" ");
}

export function moveNode(nodes: VNode[], i: number, to: Pt): VNode[] {
  return nodes.map((n, k) => (k === i ? { ...n, x: to.x, y: to.y } : n));
}

export function moveHandle(nodes: VNode[], i: number, which: "hin" | "hout", to: Pt, mirror = true): VNode[] {
  return nodes.map((n, k) => {
    if (k !== i) return n;
    const d: [number, number] = [to.x - n.x, to.y - n.y];
    const other = which === "hin" ? "hout" : "hin";
    return mirror ? { ...n, [which]: d, [other]: [-d[0], -d[1]] } : { ...n, [which]: d };
  });
}

export function circleNodes(cx: number, cy: number, r: number): VNode[] {
  const k = 0.5523 * r;
  return [
    { x: cx + r, y: cy, hin: [0, -k], hout: [0, k] },
    { x: cx, y: cy + r, hin: [k, 0], hout: [-k, 0] },
    { x: cx - r, y: cy, hin: [0, k], hout: [0, -k] },
    { x: cx, y: cy - r, hin: [-k, 0], hout: [k, 0] },
  ];
}

export function boneLength(head: number[], tail: number[]): number {
  return Math.hypot(tail[0] - head[0], tail[1] - head[1]);
}

export interface TreeGroup { id: string; name: string; parent: string | null; children: TreeGroup[]; layers: string[] }

/** Group tree with layers in back-to-front order inside each group. */
export function buildTree(groups: Record<string, { name: string; parent: string | null }>,
                          layers: { id: string; group: string }[]): TreeGroup {
  const nodes: Record<string, TreeGroup> = {};
  for (const [id, g] of Object.entries(groups)) nodes[id] = { id, name: g.name, parent: g.parent, children: [], layers: [] };
  for (const n of Object.values(nodes)) if (n.parent && nodes[n.parent]) nodes[n.parent].children.push(n);
  for (const l of layers) (nodes[l.group] ?? nodes["CHARACTER"])?.layers.push(l.id);
  return nodes["CHARACTER"];
}

/** Draw-order index of every layer in a tree (top of the list = front-most, like animation tools). */
export function frontToBack<T>(order: T[]): T[] {
  return [...order].reverse();
}

/** Small stable string hash (djb2), base-36. */
export function hashString(s: string): string {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

/** Cache key for the pixels of every bitmap layer: only mask edits and rejections change layer pixels.
 *  Bone, vector, pose or approval-only edits keep the key, so the canvas does not reload 30+ images. */
export function pixelKey(maskOps: unknown[], approvals: Record<string, { status: string }>): string {
  const rejected = Object.keys(approvals).filter((k) => approvals[k].status === "rejected").sort();
  return hashString(JSON.stringify([maskOps, rejected]));
}
