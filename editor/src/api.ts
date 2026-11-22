// Thin client for the FastAPI backend. All state lives on the backend; the browser only sends operations.

export interface Layer {
  id: string; name: string; group: string; visible: boolean; locked: boolean; opacity: number; role: string;
  bone: string | null; kind: "bitmap" | "switch"; provenance: string; user_category: string; edge_feather: number;
  extensions: string[]; file: string; duplicate_of?: string;
}
export interface Bone {
  head: [number, number]; tail: [number, number]; parent: string | null; kind: string; side: string | null;
  limits: Record<string, [number, number]>; band?: [number, number]; method?: string; edited?: boolean;
}
export interface Vector {
  id: string; name: string; purpose: string; target?: string | null; nodes: { x: number; y: number; hin?: [number, number]; hout?: [number, number] }[];
  closed: boolean; fill: string; stroke: string; stroke_width: number; visible: boolean; origin: string; status: string;
}
export interface EditorState {
  canvas: { width: number; height: number };
  layers: Layer[];
  groups: Record<string, { name: string; parent: string | null }>;
  bones: Record<string, Bone>;
  vectors: Record<string, Vector>;
  mask_ops: any[];
  corrections: { gussets: boolean; joints: Record<string, any> };
  defects: Record<string, { joint: string; preset: string; status: string; note: string }>;
  approvals: Record<string, { status: string; type?: string; note?: string }>;
  pose: { preset: string; overrides: Record<string, number> };
  ik: Record<string, any> & { pelvis_offset: [number, number] };
  switches: Record<string, { user: string | null; auto: boolean; keys: [number, string][]; offsets: Record<string, { dx: number; dy: number; rot: number }> }>;
  hand_store?: { use: Record<string, string>; placement: Record<string, any>; mask: Record<string, any>; labels: Record<string, string>; deleted: string[]; versions: Record<string, number>; libraries?: Record<string, string> };
  hand_selection?: { actions: Record<string, string | null>; targets: Record<string, [number, number] | null>; weights: Record<string, number> };
}
export interface Payload {
  project: { cursor: number; ops: number; can_undo: boolean; can_redo: boolean; revision: string; versions: any[] };
  state: EditorState;
  mask_validation: { unassigned_px: number; overlap_px: number; reassigned_px: number; layers: Record<string, any>; warnings: string[] };
  bone_warnings: { level: string; bone: string | null; issue: string }[];
  base_bones: Record<string, Bone>;
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail ?? msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

const post = (url: string, body?: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

export const api = {
  project: () => fetch("/api/project").then((r) => j<Payload>(r)),
  op: (op: Record<string, unknown>) => post("/api/op", op).then((r) => j<Payload>(r)),
  undo: () => post("/api/undo").then((r) => j<Payload>(r)),
  redo: () => post("/api/redo").then((r) => j<Payload>(r)),
  saveVersion: (label: string) => post("/api/versions", { label }).then((r) => j<any>(r)),
  restoreVersion: (n: number) => post(`/api/versions/${n}/restore`).then((r) => j<Payload>(r)),
  pose: (body: Record<string, unknown>) => post("/api/pose", body).then((r) => j<any>(r)),
  presets: () => fetch("/api/presets").then((r) => j<any[]>(r)),
  cycleStart: (body: Record<string, unknown>) => post("/api/pose/cycle", body).then((r) => j<any>(r)),
  cycleStatus: (id: string) => fetch(`/api/pose/cycle/${id}`).then((r) => j<any>(r)),
  cycleFrameUrl: (id: string, i: number) => `/api/pose/cycle/${id}/frame/${i}.png`,
  cycleAnimUrl: (id: string, ext: "webp" | "gif") => `/api/pose/cycle/${id}/animation.${ext}`,
  ikMatch: (body: Record<string, unknown>) => post("/api/ik/match", body).then((r) => j<any>(r)),
  switches: () => fetch("/api/switches").then((r) => j<any>(r)),
  switchThumbUrl: (group: string, variant: string, rev: string) => `/api/switch/thumb/${group}/${variant}.png?rev=${rev}`,
  rigControls: () => fetch("/api/rig/controls").then((r) => j<any>(r)),
  liveActions: () => fetch("/api/live/actions").then((r) => j<Record<string, any>>(r)),
  liveAction: (body: Record<string, unknown>) => post("/api/live/action", body).then((r) => j<any>(r)),
  handstore: () => fetch("/api/handstore").then((r) => j<any>(r)),
  garments: () => fetch("/api/garments").then((r) => j<any>(r)),
  correctives: () => fetch("/api/correctives").then((r) => j<any>(r)),
  artistic: () => fetch("/api/artistic").then((r) => j<any>(r)),
  performance: () => fetch("/api/performance").then((r) => j<any>(r)),
  performanceScenario: (name: string) => post("/api/performance/scenario", { name }).then((r) => j<any>(r)),
  performanceTrack: () => post("/api/performance/track", {}).then((r) => j<any>(r)),
  performanceRender: () => post("/api/performance/render", {}).then((r) => j<any>(r)),
  performanceFrameUrl: (t: number, rev: string) => `/api/performance/frame.png?t=${t.toFixed(4)}&rev=${rev}`,
  artisticCandidates: (action: string) => post("/api/artistic/candidates", { action }).then((r) => j<any>(r)),
  artisticParse: (text: string, action: string) => post("/api/artistic/feedback/parse", { text, action }).then((r) => j<any>(r)),
  correctivesGenerate: () => post("/api/correctives/generate").then((r) => j<any>(r)),
  correctivesReplace: (asset: string, png: string, note: string) => post("/api/correctives/replace", { asset, png, note }).then(async (r) => ({ ok: r.ok, body: await r.json() })),
  correctiveImageUrl: (path: string, rev: string) => `/api/correctives/image?path=${encodeURIComponent(path)}&rev=${rev}`,
  garmentOverlayUrl: (kind: string, layer: string | null, bone: string | null, rev: string) =>
    `/api/garments/overlay.png?kind=${kind}${layer ? `&layer=${encodeURIComponent(layer)}` : ""}${bone ? `&bone=${encodeURIComponent(bone)}` : ""}&rev=${rev}`,
  garmentExplain: (layer: string, x: number, y: number) => post("/api/garments/explain", { layer, x, y }).then((r) => j<any>(r)),
  garmentCompareUrl: (preset: string, rev: string) => `/api/garments/compare.png?preset=${encodeURIComponent(preset)}&rev=${rev}`,
  handstoreImport: (name: string, data_b64: string) => post("/api/handstore/import", { name, data_b64 }).then(async (r) => ({ ok: r.ok, body: await r.json() })),
  handstoreReplace: (uid: string, name: string, data_b64: string) => post("/api/handstore/replace", { uid, name, data_b64 }).then(async (r) => ({ ok: r.ok, body: await r.json() })),
  handSourceUrl: (uid: string, v?: number) => `/api/handstore/source/${uid}.png${v ? `?v=${v}` : ""}`,
  handPreviewUrl: (side: string, cid: string, rev: string) => `/api/handstore/preview/${side}/${cid}.png?rev=${rev}`,
  face: () => fetch("/api/face").then((r) => j<any>(r)),
  live: () => fetch("/api/live").then((r) => j<any>(r)),
  gates: () => fetch("/api/gates").then((r) => j<any>(r)),
  metrics: () => fetch("/api/compare/metrics").then((r) => j<any>(r)),
  exportPkg: () => post("/api/export").then((r) => j<any>(r)),
  layerUrl: (id: string, rev: string) => `/api/layer/${encodeURIComponent(id)}.png?rev=${rev}`,
  maskUrl: (id: string, rev: string, base = false) => `/api/mask/${encodeURIComponent(id)}.png?rev=${rev}&base=${base}`,
  compareUrl: (kind: string, rev: string) => `/api/compare/${kind}.png?rev=${rev}`,
  sourceUrl: () => "/api/source.png",
};
