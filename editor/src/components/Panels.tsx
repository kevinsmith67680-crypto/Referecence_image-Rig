import { useEffect, useState } from "react";
import { api, Payload } from "../api";
import { boneLength, circleNodes } from "../geometry";
import type { CompareSettings, MaskSettings, Overlays, Tool } from "./CanvasView";

type OnOp = (op: Record<string, unknown>) => void;
const badge = (s: string) => <span className={`badge b-${(s || "").toLowerCase()}`}>{s}</span>;

export function PropertiesPanel({ payload, id, onOp }: { payload: Payload; id: string | null; onOp: OnOp }) {
  const L = payload.state.layers.find((l) => l.id === id);
  if (!L) return <div className="muted">Select a layer (Select tool, click the artwork or a row in Layers).</div>;
  const set = (fields: any) => onOp({ type: "layer.set", id: L.id, fields });
  const v = payload.mask_validation.layers[L.id];
  return (
    <div className="form" data-testid="properties">
      <label>Name <input defaultValue={L.name} key={L.id + L.name} data-testid="prop-name"
        onBlur={(e) => e.target.value !== L.name && set({ name: e.target.value })} /></label>
      <label>Anatomical role <input defaultValue={L.role} key={L.id + L.role} onBlur={(e) => e.target.value !== L.role && set({ role: e.target.value })} /></label>
      <label>Bone binding <select value={L.bone ?? ""} onChange={(e) => set({ bone: e.target.value || null })}>
        <option value="">(none)</option>{Object.keys(payload.state.bones).map((b) => <option key={b}>{b}</option>)}</select></label>
      <label>Category tag <select value={L.user_category} onChange={(e) => set({ user_category: e.target.value })}>
        {["original", "extracted", "reconstructed", "vector_corrected", "reference_derived", "approximation"].map((c) => <option key={c}>{c}</option>)}</select></label>
      {L.user_category === "original" && L.provenance.includes("approximation") && <div className="warn-box">Tagged original but its provenance is {L.provenance}.</div>}
      <label>Opacity {L.opacity.toFixed(2)} <input type="range" min={0} max={1} step={0.05} defaultValue={L.opacity} key={L.id + "op" + L.opacity}
        onMouseUp={(e) => set({ opacity: +(e.target as HTMLInputElement).value })} data-testid="prop-opacity" /></label>
      <label>Edge feather (export) {L.edge_feather}px <input type="range" min={0} max={3} step={0.5} defaultValue={L.edge_feather} key={L.id + "ef"}
        onMouseUp={(e) => set({ edge_feather: +(e.target as HTMLInputElement).value })} /></label>
      <div className="row gap">
        <label className="check"><input type="checkbox" checked={L.visible} onChange={(e) => set({ visible: e.target.checked })} /> visible</label>
        <label className="check"><input type="checkbox" checked={L.locked} onChange={(e) => set({ locked: e.target.checked })} /> locked</label>
      </div>
      <div className="kv"><b>Provenance</b> {L.provenance}</div>
      <div className="kv"><b>Kind</b> {L.kind}{L.kind === "switch" ? " (switch drawing)" : ""}</div>
      {v && <div className="kv"><b>Pixels</b> {v.pixels} (segmentation {v.base_pixels}) {v.warnings.map((w: string) => <div key={w} className="warn-box">{w}</div>)}</div>}
      {L.extensions.length > 0 && <div><b>Generated extensions (under the original)</b>
        {L.extensions.map((e) => <div key={e} className="row gap">{e} {badge(payload.state.approvals[e]?.status ?? "pending_review")}
          <button onClick={() => onOp({ type: "approval.set", item: e, status: "approved" })}>Approve</button>
          <button onClick={() => onOp({ type: "approval.set", item: e, status: "rejected" })}>Reject</button></div>)}</div>}
    </div>
  );
}

export function MaskPanel({ payload, id, tool, setTool, mask, setMask, overlays, setOverlays, onOp }: {
  payload: Payload; id: string | null; tool: Tool; setTool: (t: Tool) => void; mask: MaskSettings; setMask: (m: MaskSettings) => void;
  overlays: Overlays; setOverlays: (o: Overlays) => void; onOp: OnOp }) {
  const layers = payload.state.layers.filter((l) => l.kind === "bitmap");
  const v = id ? payload.mask_validation.layers[id] : null;
  return (
    <div className="form" data-testid="mask-panel">
      <div className="muted">Masks move <b>original source pixels</b> between layers; nothing is painted or invented.</div>
      <div className="kv"><b>Layer</b> {id ?? "(select a layer)"}</div>
      <div className="row gap">
        {(["brush", "eraser", "polygon"] as Tool[]).map((t) => <button key={t} className={tool === t ? "on" : ""} onClick={() => setTool(t)}
          data-testid={`tool-${t}`}>{t === "brush" ? "Brush (add)" : t === "eraser" ? "Eraser (subtract)" : "Polygon"}</button>)}
      </div>
      <label>Brush size {mask.size}px <input type="range" min={2} max={80} value={mask.size} onChange={(e) => setMask({ ...mask, size: +e.target.value })} /></label>
      <label>Hardness {mask.hardness.toFixed(2)} <input type="range" min={0} max={1} step={0.05} value={mask.hardness} onChange={(e) => setMask({ ...mask, hardness: +e.target.value })} /></label>
      <label>Removed pixels go to <select value={mask.transferTo} onChange={(e) => setMask({ ...mask, transferTo: e.target.value })} data-testid="transfer-to">
        <option value="">(unassigned: creates a gap)</option>{layers.filter((l) => l.id !== id).map((l) => <option key={l.id} value={l.id}>{l.id}</option>)}</select></label>
      {tool === "polygon" && <div className="row gap">
        <button onClick={() => (window as any).__autorigClosePolygon?.("add")} data-testid="poly-add">Add polygon</button>
        <button onClick={() => (window as any).__autorigClosePolygon?.("subtract")} data-testid="poly-sub">Subtract polygon</button>
        <span className="muted">(click points; Enter = add, Shift+Enter = subtract)</span></div>}
      <div className="row gap">
        <label className="check"><input type="checkbox" checked={overlays.masks} onChange={(e) => setOverlays({ ...overlays, masks: e.target.checked })} /> show mask</label>
        <label className="check"><input type="checkbox" checked={overlays.baseMask} onChange={(e) => setOverlays({ ...overlays, baseMask: e.target.checked })} data-testid="before-after" /> before (SAM 3 mask)</label>
      </div>
      <div className="row gap">
        <button disabled={!id} onClick={() => id && onOp({ type: "mask.refine", layer: id })}>Refine edges</button>
        <button disabled={!id} onClick={() => id && onOp({ type: "mask.reset", layer: id })} data-testid="mask-reset">Restore original mask</button>
      </div>
      <div className="muted">Edge feathering is a layer property (Properties tab); masks stay binary so every source pixel is in at most one layer.</div>
      {v && <div className="kv"><b>Validation</b> {v.pixels}px (was {v.base_pixels}), {v.pieces} piece(s) {v.warnings.map((w: string) => <div key={w} className="warn-box">{w}</div>)}</div>}
      <div className="kv"><b>Character</b> unassigned {payload.mask_validation.unassigned_px}px · reassigned {payload.mask_validation.reassigned_px}px · overlap {payload.mask_validation.overlap_px}px</div>
      {payload.mask_validation.warnings.map((w) => <div key={w} className="warn-box">{w}</div>)}
    </div>
  );
}

export function JointsPanel({ payload, bone, onSelect, onOp }: { payload: Payload; bone: string | null; onSelect: (b: string) => void; onOp: OnOp }) {
  const B = payload.state.bones;
  const b = bone ? B[bone] : null;
  const base = bone ? payload.base_bones[bone] : null;
  const num = (v: number) => +v.toFixed(1);
  return (
    <div className="form" data-testid="joints-panel">
      <div className="muted">Use the Joint tool (J) to drag pivots (circles) and end points (squares). Values are source pixels.</div>
      <select value={bone ?? ""} onChange={(e) => onSelect(e.target.value)} data-testid="bone-select">
        <option value="">(select a bone)</option>{Object.keys(B).map((n) => <option key={n}>{n}</option>)}</select>
      {b && bone && <>
        <div className="row gap">
          <label>Pivot x <input type="number" defaultValue={num(b.head[0])} key={bone + "hx" + b.head[0]} onBlur={(e) => onOp({ type: "bone.set", name: bone, fields: { head: [+e.target.value, b.head[1]] } })} /></label>
          <label>y <input type="number" defaultValue={num(b.head[1])} key={bone + "hy" + b.head[1]} onBlur={(e) => onOp({ type: "bone.set", name: bone, fields: { head: [b.head[0], +e.target.value] } })} /></label>
        </div>
        <div className="row gap">
          <label>Tail x <input type="number" defaultValue={num(b.tail[0])} key={bone + "tx" + b.tail[0]} onBlur={(e) => onOp({ type: "bone.set", name: bone, fields: { tail: [+e.target.value, b.tail[1]] } })} /></label>
          <label>y <input type="number" defaultValue={num(b.tail[1])} key={bone + "ty" + b.tail[1]} onBlur={(e) => onOp({ type: "bone.set", name: bone, fields: { tail: [b.tail[0], +e.target.value] } })} /></label>
        </div>
        <label>Length <input type="number" defaultValue={num(boneLength(b.head, b.tail))} key={bone + "len" + b.tail} onBlur={(e) => onOp({ type: "bone.set", name: bone, fields: { length: +e.target.value } })} /></label>
        <label>Parent <select value={b.parent ?? ""} onChange={(e) => onOp({ type: "bone.set", name: bone, fields: { parent: e.target.value || null } })}>
          <option value="">(root)</option>{Object.keys(B).filter((n) => n !== bone).map((n) => <option key={n}>{n}</option>)}</select></label>
        <b>Rotation constraints (deg)</b>
        {Object.entries(b.limits ?? {}).map(([k, r]) => (
          <div key={k} className="row gap">{k} <input type="number" defaultValue={r[0]} key={bone + k + "lo" + r[0]} onBlur={(e) => onOp({ type: "bone.set", name: bone, fields: { limits: { [k]: [+e.target.value, r[1]] } } })} />
            to <input type="number" defaultValue={r[1]} key={bone + k + "hi" + r[1]} onBlur={(e) => onOp({ type: "bone.set", name: bone, fields: { limits: { [k]: [r[0], +e.target.value] } } })} /></div>))}
        {base && <div className="kv"><b>Analysed</b> pivot ({num(base.head[0])}, {num(base.head[1])}) · moved {num(Math.hypot(b.head[0] - base.head[0], b.head[1] - base.head[1]))}px · {base.method}</div>}
        <button onClick={() => onOp({ type: "bone.reset", name: bone })} data-testid="bone-reset">Reset to analysed position</button>
      </>}
      <b>Warnings</b>
      {payload.bone_warnings.length === 0 && <div className="ok-box">No hierarchy, length or position problems.</div>}
      {payload.bone_warnings.map((w, i) => <div key={i} className={w.level === "error" ? "err-box" : "warn-box"}>{w.bone ?? ""} {w.issue}</div>)}
    </div>
  );
}

export function VectorsPanel({ payload, sel, onSelect, bone, onOp }: { payload: Payload; sel: string | null; onSelect: (v: string | null) => void; bone: string | null; onOp: OnOp }) {
  const V = payload.state.vectors;
  const v = sel ? V[sel] : null;
  const create = () => {
    const c = bone ? payload.state.bones[bone].head : [payload.state.canvas.width / 2, payload.state.canvas.height / 2];
    const id = `shape_${Date.now() % 100000}`;
    onOp({ type: "vector.create", vector: { id, name: id, purpose: "joint correction", target: bone, nodes: circleNodes(c[0], c[1], 30),
      closed: true, fill: "rgb(42,39,48)", stroke: "rgb(8,8,10)", stroke_width: 4 } });
    onSelect(id);
  };
  return (
    <div className="form" data-testid="vectors-panel">
      <div className="muted">Vector corrections sit on top of the bitmap art (joint caps, flat fills, guides). They never replace original pixels. Use the Vector tool (N) to drag anchors (squares) and Bezier handles (dots).</div>
      <div className="vlist">{Object.values(V).map((x) => (
        <div key={x.id} className={`layer-row ${sel === x.id ? "sel" : ""}`} onClick={() => onSelect(x.id)} data-testid={`vec-${x.id}`}>
          {x.visible ? "◉" : "○"} {x.name} <span className="muted">{x.origin}</span> {badge(payload.state.approvals[`vector:${x.id}`]?.status ?? x.status)}</div>))}</div>
      <button onClick={create} data-testid="vec-new">New correction shape {bone ? `at ${bone}` : ""}</button>
      {v && sel && <>
        <label>Fill <input defaultValue={v.fill} key={sel + v.fill} onBlur={(e) => onOp({ type: "vector.update", id: sel, fields: { fill: e.target.value } })} /></label>
        <label>Stroke <input defaultValue={v.stroke} key={sel + v.stroke} onBlur={(e) => onOp({ type: "vector.update", id: sel, fields: { stroke: e.target.value } })} /></label>
        <label>Stroke width <input type="number" step={0.5} defaultValue={v.stroke_width} key={sel + v.stroke_width} onBlur={(e) => onOp({ type: "vector.update", id: sel, fields: { stroke_width: +e.target.value } })} /></label>
        <div className="row gap">
          <label className="check"><input type="checkbox" checked={v.closed} onChange={(e) => onOp({ type: "vector.update", id: sel, fields: { closed: e.target.checked } })} /> closed</label>
          <label className="check"><input type="checkbox" checked={v.visible} onChange={(e) => onOp({ type: "vector.update", id: sel, fields: { visible: e.target.checked } })} /> visible</label>
        </div>
        <div className="kv"><b>Nodes</b> {v.nodes.length} · origin {v.origin} · target {v.target ?? "-"}</div>
        <div className="row gap">
          <button onClick={() => onOp({ type: "approval.set", item: `vector:${sel}`, status: "approved" })}>Approve</button>
          <button onClick={() => onOp({ type: "approval.set", item: `vector:${sel}`, status: "rejected" })}>Reject</button>
          <button onClick={() => { onOp({ type: "vector.delete", id: sel }); onSelect(null); }}>Delete</button>
        </div>
      </>}
    </div>
  );
}

const POSE_BONES = ["head", "neck", "chest", "spine", "pelvis", "clavicle_r", "clavicle_l", "upper_arm_r", "upper_arm_l",
  "forearm_r", "forearm_l", "hand_r", "hand_l", "thigh_r", "thigh_l", "shin_r", "shin_l", "foot_r", "foot_l"];

export interface Motion { phase: number; speed: number; exaggeration: number; allowPending: boolean; diagnostics: string[];
  before: boolean; showBefore: boolean; liveTilt: number }
export interface Perf { fps: number; latencyMs: number; solveMs: number; frames: number }
const DIAG = [["skeleton", "anatomical skeleton"], ["garments", "garment regions + control points"], ["boundaries", "mesh / part boundaries"],
  ["weights", "deformation weights"], ["overlaps", "joint overlap regions"], ["contacts", "foot contacts"], ["com", "centre of mass"],
  ["provenance", "generated pixels"], ["missing", "missing artwork"], ["correctives", "corrective influence"]];
const CLOSE = [["upper_arm_r", "shoulder R"], ["upper_arm_l", "shoulder L"], ["forearm_r", "elbow R"], ["forearm_l", "elbow L"],
  ["pelvis", "hips / hem"], ["shin_r", "knee R"], ["shin_l", "knee L"], ["foot_r", "shoe R"], ["foot_l", "shoe L"]];

export function PosePanel({ payload, preset, setPreset, overrides, setOverrides, result, busy, bone, onSelectBone, overlays, setOverlays, onOp,
  motion, setMotion, playing, setPlaying, perf, onCloseup }: {
  payload: Payload; preset: string; setPreset: (p: string) => void; overrides: Record<string, number>; setOverrides: (o: Record<string, number>) => void;
  result: any; busy: boolean; bone: string | null; onSelectBone: (b: string) => void; overlays: Overlays; setOverlays: (o: Overlays) => void; onOp: OnOp;
  motion: Motion; setMotion: (m: Motion) => void; playing: boolean; setPlaying: (p: boolean) => void; perf: Perf; onCloseup: (bone: string) => void }) {
  const [presets, setPresets] = useState<any[]>([]);
  useEffect(() => { api.presets().then(setPresets); }, []);
  const corr = bone ? payload.state.corrections.joints[bone] : null;
  const band = (bone && (corr?.per_pose?.[preset]?.band ?? corr?.band ?? payload.state.bones[bone]?.band)) || [20, 20];
  const [bandLocal, setBandLocal] = useState<number[]>(band);
  useEffect(() => setBandLocal(band), [bone, preset, payload.project.revision]);
  const seams = result?.metrics?.seams?.joints ?? {};
  const kind = presets.find((p) => p.name === preset)?.kind;
  const f = result?.feasibility;
  const set = (k: Partial<Motion>) => setMotion({ ...motion, ...k });
  return (
    <div className="form" data-testid="pose-panel">
      <label>Pose <select value={preset} onChange={(e) => { setPlaying(false); setPreset(e.target.value); setOverrides({}); }} data-testid="preset">
        {presets.map((p) => <option key={p.name} value={p.name}>{p.title}{p.kind === "gait" ? " (cycle)" : ""}</option>)}</select></label>
      <div className="muted">Motion planner → skeletal solver (IK, contacts, balance) → garment-aware deformation → checks. Sliders below add direct rotations.</div>
      {kind === "gait" && <div className="box" data-testid="gait-controls">
        <label>Phase {motion.phase.toFixed(3)} {result?.phase_name ? `(${(playing ? "" : result.phase_name)})` : ""}
          <input type="range" min={0} max={0.999} step={0.001} value={motion.phase} data-testid="gait-phase"
            onChange={(e) => set({ phase: +e.target.value })} /></label>
        <label>Speed {motion.speed.toFixed(2)} <input type="range" min={0.5} max={1.6} step={0.05} value={motion.speed} data-testid="gait-speed"
          onChange={(e) => set({ speed: +e.target.value })} /></label>
        <label>Exaggeration {motion.exaggeration.toFixed(2)} <input type="range" min={0} max={1.6} step={0.05} value={motion.exaggeration}
          data-testid="gait-exaggeration" onChange={(e) => set({ exaggeration: +e.target.value })} /></label>
        <div className="row gap"><button onClick={() => setPlaying(!playing)} data-testid="gait-play">{playing ? "■ Stop" : "▶ Play cycle (skeleton)"}</button>
          <span className="small" data-testid="perf">{playing || perf.frames ? `${perf.fps} fps · latency ${perf.latencyMs} ms · solve ${perf.solveMs} ms` : ""}</span></div>
      </div>}
      <div className="row gap wrap">
        <label className="check"><input type="checkbox" checked={motion.allowPending} onChange={(e) => set({ allowPending: e.target.checked })} data-testid="allow-pending" /> preview pending artwork</label>
        <label className="check"><input type="checkbox" checked={motion.before} onChange={(e) => set({ before: e.target.checked, showBefore: false })} data-testid="before-after" /> compare with Phase 3</label>
        {motion.before && <label className="check"><input type="checkbox" checked={motion.showBefore} onChange={(e) => set({ showBefore: e.target.checked })} data-testid="show-before" /> show Phase 3 render</label>}
      </div>
      <label>Live head tilt (puppeteering channel) {motion.liveTilt}° <input type="range" min={-20} max={20} step={1} value={motion.liveTilt}
        onChange={(e) => set({ liveTilt: +e.target.value })} data-testid="live-tilt" /></label>
      <div className="kv" data-testid="pose-status"><b>Status</b> {busy ? "rendering…" : result ? <>{badge(result.status)} {result.confidence}
        {result.timing_ms && <span className="muted small"> · solve {result.timing_ms.solve} ms · render {result.timing_ms.render_and_tears ?? "-"} ms · round trip {result.roundtrip_ms ?? "-"} ms</span>}</> : "-"}</div>
      {f && <div className="box" data-testid="feasibility">
        <b>Feasibility</b> {badge(f.status)}
        {f.missing?.map((m: any) => <div key={m.asset} className="err-box">missing: {m.asset} ({m.state}) — {m.why}</div>)}
        {f.alternative && <div className="row gap">Supported alternative: <button onClick={() => setPreset(f.alternative)} data-testid="use-alternative">{f.alternative}</button></div>}
        {f.reason && <div className="muted small">{f.reason}</div>}
        {result?.hands && Object.entries(result.hands).filter(([, h]: any) => h.note).map(([s, h]: any) => <div key={s} className="warn-box">hand {s}: {h.note}</div>)}
        {f.corrections?.length ? <div className="muted small">corrections: {f.corrections.join(", ")}</div> : null}
      </div>}
      {result?.quality && <details data-testid="quality"><summary>Automated checks {result.quality.numerical.passed}/{result.quality.numerical.total} · visual quality not scored (review required)</summary>
        <table><tbody>{result.quality.checks.map((c: any) => <tr key={c.check}><td>{c.pass ? "✓" : "✗"}</td><td>{c.check}</td><td>{c.value}</td><td className="muted">≤ {c.threshold}</td></tr>)}</tbody></table>
        {result.quality.review_items.map((d: string) => <div key={d} className="warn-box">{d}</div>)}
      </details>}
      <b>Diagnostics</b>
      <div className="row gap wrap">{DIAG.map(([k, label]) => <label key={k} className="check small"><input type="checkbox" data-testid={`diag-${k}`}
        checked={motion.diagnostics.includes(k)} onChange={(e) => set({ diagnostics: e.target.checked ? [...motion.diagnostics, k] : motion.diagnostics.filter((x) => x !== k) })} />{label}</label>)}
        <label className="check small"><input type="checkbox" checked={overlays.tears} onChange={(e) => setOverlays({ ...overlays, tears: e.target.checked })} /> seam markers</label>
        <label className="check small"><input type="checkbox" checked={overlays.boundaries} onChange={(e) => setOverlays({ ...overlays, boundaries: e.target.checked })} data-testid="boundaries" /> part boundaries</label>
        <label className="check small"><input type="checkbox" checked={payload.state.corrections.gussets} onChange={(e) => onOp({ type: "correction.gussets", enabled: e.target.checked })} /> armpit gussets</label></div>
      <div className="row gap wrap">Close-up: {CLOSE.map(([b, l]) => <button key={b} className="small" onClick={() => onCloseup(b)} data-testid={`close-${b}`}>{l}</button>)}</div>
      <details><summary>Direct rotations (manual)</summary>
      <div className="sliders">{POSE_BONES.filter((b) => payload.state.bones[b]).map((b) => (
        <label key={b} className="slider">{b} <input type="range" min={-120} max={120} step={1} value={overrides[b] ?? 0} data-testid={`rot-${b}`}
          onChange={(e) => setOverrides({ ...overrides, [b]: +e.target.value })} /> <span>{overrides[b] ?? 0}°</span></label>))}</div>
      <button onClick={() => setOverrides({})}>Reset rotations</button></details>
      {result && <>
        {result.violations?.map((v: string) => <div key={v} className="err-box">constraint: {v}</div>)}
        {result.defects?.slice(0, 6).map((d: string) => <div key={d} className="warn-box">{d}</div>)}
      </>}
      <b>Joint deformation correction</b>
      <select value={bone ?? ""} onChange={(e) => onSelectBone(e.target.value)} data-testid="corr-joint">
        <option value="">(select a joint)</option>{Object.keys(payload.state.bones).map((n) => <option key={n} value={n}>{n}{seams[n.replace("upper_arm", "shoulder").replace("forearm", "elbow").replace("thigh", "hip").replace("shin", "knee")] ? " ⚠" : ""}</option>)}</select>
      {bone && <>
        <label>Blend into parent {bandLocal[0].toFixed(0)}px <input type="range" min={2} max={160} value={bandLocal[0]} onChange={(e) => setBandLocal([+e.target.value, bandLocal[1]])} data-testid="band-parent" /></label>
        <label>Blend into child {bandLocal[1].toFixed(0)}px <input type="range" min={2} max={160} value={bandLocal[1]} onChange={(e) => setBandLocal([bandLocal[0], +e.target.value])} /></label>
        <div className="row gap">
          <button onClick={() => onOp({ type: "correction.set", joint: bone, preset, fields: { band: bandLocal } })} data-testid="corr-save-pose">Save for this pose</button>
          <button onClick={() => onOp({ type: "correction.set", joint: bone, fields: { band: bandLocal } })}>Save for all poses</button>
        </div>
        <div className="row gap">
          {(["unresolved", "resolved", "accepted"] as const).map((s) => <button key={s} data-testid={`defect-${s}`}
            onClick={() => onOp({ type: "defect.set", id: `${preset}:${bone}`, joint: bone, preset, status: s, note: "" })}>Mark {s}</button>)}
        </div>
        {payload.state.defects[`${preset}:${bone}`] && <div className="kv"><b>Defect</b> {badge(payload.state.defects[`${preset}:${bone}`].status)}</div>}
        <div className="muted">Corrections are metadata for Phase 4 (bone influence / Smart Warp / Smart Bone actions); they are not Moho Smart Bones.</div>
      </>}
    </div>
  );
}

export function FacePanel({ face, overlays, setOverlays, onOp }: { face: any; overlays: Overlays; setOverlays: (o: Overlays) => void; onOp: OnOp }) {
  if (!face) return <div className="muted">Loading…</div>;
  return (
    <div className="form" data-testid="face-panel">
      <label className="check"><input type="checkbox" checked={overlays.anchors} onChange={(e) => setOverlays({ ...overlays, anchors: e.target.checked })} /> show facial anchors and pivots</label>
      <table><thead><tr><th>Feature</th><th>Pixels</th><th>Review</th></tr></thead><tbody>
        {face.features.map((f: any) => <tr key={f.id}><td>{f.id}{f.note ? <div className="muted">{f.note}</div> : null}</td><td>{f.present ? f.pixels : "missing"}</td>
          <td>{badge(f.approval)} <button onClick={() => onOp({ type: "approval.set", item: `face:${f.id}`, status: "approved" })}>✓</button></td></tr>)}
      </tbody></table>
      <b>Facial animation readiness</b>
      <table><thead><tr><th>Capability</th><th>Status</th><th>Missing</th></tr></thead><tbody>
        {face.capabilities.map((c: any) => <tr key={c.capability} data-testid={`cap-${c.capability}`}><td>{c.capability}</td><td>{badge(c.readiness)}</td>
          <td className="small">{c.missing.join(", ") || "-"}</td></tr>)}
      </tbody></table>
      <div className="muted">{face.rule}</div>
    </div>
  );
}

export function LivePanel({ live }: { live: any }) {
  if (!live) return <div className="muted">Loading…</div>;
  return (
    <div className="form" data-testid="live-panel">
      <div className="muted">{live.rule}. Webcam tracking: {live.webcam_tracking}.</div>
      <table><thead><tr><th>Control</th><th>Artwork</th><th>Missing</th><th>Type</th><th>Approval</th><th>Moho mapping</th><th>Live</th></tr></thead><tbody>
        {live.controls.map((c: any) => <tr key={c.control}><td>{c.control}</td><td>{badge(c.readiness)}</td><td className="small">{c.missing.join(", ") || "-"}</td>
          <td>{c.proposed_control_type}</td><td>{badge(c.user_approval)}</td><td>{badge(c.moho_mapping)}</td><td title={c.why_not_live_ready}>{c.live_ready ? "yes" : "no"}</td></tr>)}
      </tbody></table>
      <b>Gamepad-triggered gestures (proposed)</b>
      <table><tbody>{live.gamepad_gestures.map((g: any) => <tr key={g.gesture}><td>{g.title}</td><td>{badge(g.pose_status)}</td><td>{badge(g.moho_mapping)}</td></tr>)}</tbody></table>
    </div>
  );
}

export function ComparePanel({ cmp, setCmp, metrics, payload, onOp }: { cmp: CompareSettings; setCmp: (c: CompareSettings) => void; metrics: any; payload: Payload; onOp: OnOp }) {
  const f = metrics?.fidelity;
  const signed = payload.state.approvals["sign_off:neutral_fidelity"]?.status;
  return (
    <div className="form" data-testid="compare-panel">
      <div className="row gap wrap">{(["split", "overlay", "diff", "provenance", "reference"] as const).map((k) => (
        <button key={k} className={cmp.kind === k ? "on" : ""} onClick={() => setCmp({ ...cmp, kind: k })} data-testid={`cmp-${k}`}>{k}</button>))}</div>
      {cmp.kind === "split" && <label>Split (left = editor neutral, right = original) <input type="range" min={0} max={1} step={0.01} value={cmp.split} onChange={(e) => setCmp({ ...cmp, split: +e.target.value })} /></label>}
      {cmp.kind === "overlay" && <label>Overlay opacity <input type="range" min={0} max={1} step={0.01} value={cmp.opacity} onChange={(e) => setCmp({ ...cmp, opacity: +e.target.value })} /></label>}
      {cmp.kind === "diff" && <div className="legend"><span><i style={{ background: "#ff0" }} />small colour error</span>
        <span><i style={{ background: "#f00" }} />large colour error (editor neutral vs original)</span><span><i style={{ background: "#f0f" }} />character pixel in no layer</span></div>}
      {cmp.kind === "provenance" && <div className="legend"><span><i style={{ background: "#0cf" }} />original pixels re-assigned by a mask edit</span>
        <span><i style={{ background: "#f0f" }} />generated pixels (hidden under art in neutral)</span><span><i style={{ background: "#f00" }} />unassigned (gap)</span></div>}
      {f && <>
        <div className="kv"><b>Neutral fidelity</b> {f.neutral_exact ? <span className="ok">exact</span> : <span className="err">differs</span>} · max error {f.max_abs_error_in_covered} · gap {f.gap_px}px of {f.footprint_px}</div>
        {metrics.mask_validation.reassigned_px > 0 && <div className="warn-box">{metrics.mask_validation.reassigned_px} original pixels moved between layers by mask corrections (pixels unchanged).</div>}
        {f.gap_px > 0 && <div className="err-box">{f.gap_px} character pixels are in no layer: the neutral pose differs from the reference.</div>}
      </>}
      <div className="row gap">Neutral fidelity sign-off {badge(signed ?? "pending_review")}
        <button onClick={() => onOp({ type: "approval.set", item: "sign_off:neutral_fidelity", status: "approved" })} data-testid="signoff">Approve</button></div>
    </div>
  );
}

export function ExportPanel({ gates, onRefresh, onExport, exportResult, payload, onOp }: { gates: any; onRefresh: () => void; onExport: () => void; exportResult: any; payload: Payload; onOp: OnOp }) {
  const pending = Object.entries(payload.state.approvals).filter(([k, v]) => !["approved", "rejected"].includes(v.status) &&
    (k.startsWith("corrective:") || k.includes("__0") || k.startsWith("vector:")));
  return (
    <div className="form" data-testid="export-panel">
      <div className="row gap"><button onClick={onRefresh}>Run quality gates</button><button onClick={onExport} data-testid="export">Export Moho-ready package</button></div>
      {gates && <table><tbody>{gates.gates.map((g: any) => <tr key={g.id}><td>{badge(g.level)}</td><td>{g.check}<div className="muted small">{g.detail}</div></td></tr>)}</tbody></table>}
      {gates && <div className="kv"><b>Production ready</b> {gates.production_ready ? <span className="ok">yes</span> : <span className="err">no ({gates.overall})</span>}</div>}
      {exportResult && <div className="ok-box" data-testid="export-result">Exported {exportResult.layers} layers, {exportResult.vectors} vectors, {exportResult.bones} bones to {exportResult.out}. Validation errors: {exportResult.validation_errors.length}</div>}
      <b>Pending approvals ({pending.length})</b>
      <div className="vlist">{pending.slice(0, 60).map(([k, v]) => <div key={k} className="row gap small">{k} {badge(v.status)}
        <button onClick={() => onOp({ type: "approval.set", item: k, status: "approved" })}>Approve</button>
        <button onClick={() => onOp({ type: "approval.set", item: k, status: "rejected" })}>Reject</button></div>)}</div>
    </div>
  );
}
