import { useEffect, useState } from "react";
import { api, Payload } from "../api";

// Phase 3.3 garment weight editor: inspect the garment parts and each bone's influence, paint / erase / smooth /
// lock weights with a brush on the canvas (ops in the project: undo/redo, versions), normalise, and ask why a
// point follows the bones it does. The source image is never modified; edits replay on the automatic weights.
export interface WeightBrush { mode: "paint" | "erase" | "smooth" | "lock" | "unlock" | "explain"; bone: string; radius: number;
  strength: number; hardness: number; part: string; view: "parts" | "influence" | "mesh" | "none" }

export function WeightsPanel({ payload, layer, rev, brush, setBrush, explain, onOp, onError, preset }: {
  payload: Payload; layer: string | null; rev: string; brush: WeightBrush; setBrush: (b: WeightBrush) => void;
  explain: any; onOp: (op: Record<string, unknown>) => void; onError: (e: string) => void; preset: string }) {
  const [info, setInfo] = useState<any>(null);
  const [cmp, setCmp] = useState(false);
  useEffect(() => { api.garments().then(setInfo).catch((e) => onError(String(e.message ?? e))); }, [rev]);
  if (!info) return <div className="panel" data-testid="weights-panel">Loading garment rig…</div>;
  if (!info.available) return <div className="panel" data-testid="weights-panel">Legacy garment mode: no weight meshes.</div>;
  const L = layer ? info.layers[layer] : null;
  const parts = info.structure.parts as any[];
  const val = info.validation;
  const myOps = (info.ops as any[]).filter((o) => o.layer === layer);
  return (
    <div className="panel" data-testid="weights-panel">
      <div className="panel-title">Garment weights</div>
      <p className="hint">Select a layer, choose a bone and a brush, then paint on the canvas (artwork view). Edits are
        project operations (undo/redo); the original artwork is never changed.</p>
      <div className="row"><b>Layer</b> <span data-testid="weights-layer">{layer ?? "— select a layer —"}</span></div>
      <label className="row">View
        <select value={brush.view} onChange={(e) => setBrush({ ...brush, view: e.target.value as WeightBrush["view"] })} data-testid="weights-view">
          <option value="parts">garment parts</option><option value="influence">bone influence (heatmap)</option>
          <option value="mesh">mesh topology</option><option value="none">none</option></select></label>
      <label className="row">Bone
        <select value={brush.bone} onChange={(e) => setBrush({ ...brush, bone: e.target.value })} data-testid="weights-bone">
          {(L?.bones ?? Object.keys(payload.state.bones)).map((b: string) => <option key={b} value={b}>{b}</option>)}</select></label>
      <div className="row">{(["paint", "erase", "smooth", "lock", "unlock", "explain"] as const).map((m) => (
        <button key={m} className={brush.mode === m ? "on" : ""} onClick={() => setBrush({ ...brush, mode: m })} data-testid={`weights-mode-${m}`}>{m}</button>))}</div>
      <label className="row">Radius {brush.radius}px<input type="range" min={4} max={120} value={brush.radius}
        onChange={(e) => setBrush({ ...brush, radius: +e.target.value })} /></label>
      <label className="row">Strength {brush.strength.toFixed(2)}<input type="range" min={0.05} max={1} step={0.05} value={brush.strength}
        onChange={(e) => setBrush({ ...brush, strength: +e.target.value })} /></label>
      <label className="row">Restrict to part
        <select value={brush.part} onChange={(e) => setBrush({ ...brush, part: e.target.value })} data-testid="weights-part">
          <option value="">(any part on the layer)</option>
          {(L?.parts ?? []).map((n: string) => <option key={n} value={n}>{n}</option>)}</select></label>
      <div className="row">
        <button disabled={!layer} onClick={() => onOp({ type: "weights.normalise", layer })} data-testid="weights-normalise">Normalise layer</button>
        <button disabled={!layer || !myOps.length} onClick={() => onOp({ type: "weights.reset", layer })} data-testid="weights-reset">Reset layer edits</button>
        <button onClick={() => setCmp(!cmp)} data-testid="weights-compare">{cmp ? "Hide" : "Compare"} auto vs edited</button>
      </div>
      {cmp && <img className="cmp" src={api.garmentCompareUrl(preset === "rest" ? "relaxed_standing" : preset, rev)} alt="automatic weights (left) vs your edits (right)" data-testid="weights-compare-img" style={{ width: "100%" }} />}
      <div className="row small">validation: {val.ok ? "OK" : `${val.n_issues} issues`} · {val.meshes} meshes · {val.vertices} vertices · edits on this layer: {myOps.length}</div>
      {L && <div className="row small">grid {L.grid.join("×")} · locked {L.locked} · edited {L.edited}</div>}
      {explain && <div className="explain" data-testid="weights-explain">
        <div className="panel-title">Why does this point move?</div>
        <div><b>{explain.part}</b> ({explain.class})</div>
        <div className="small">{explain.why}</div>
        <div className="small">weights: {Object.entries(explain.weights ?? {}).map(([b, v]: any) => `${b} ${(+v).toFixed(2)}`).join(", ")}</div>
        <div className="small">{explain.locked ? "locked vertex" : ""} {explain.user_edits ? `${explain.user_edits} edit(s)` : ""}</div>
      </div>}
      <div className="panel-title">Pivots</div>
      <table className="small"><tbody>{(info.pivots as any[]).filter((p) => p.joint.startsWith("shoulder") || p.status === "warning").map((p) => (
        <tr key={p.joint}><td>{p.joint}</td><td>{p.pivot?.map((v: number) => v.toFixed(0)).join(", ")}</td>
          <td>{p.aligned ? `lifted ${p.lift_px}px` : p.status ?? (p.reason ?? "")}</td></tr>))}</tbody></table>
      <p className="hint">Move a pivot in the Joints tab; an edited bone keeps your position.</p>
      <div className="panel-title">Parts ({parts.length})</div>
      <div className="parts small">{parts.map((p) => <span key={p.name} className={`chip c-${p.class}`} title={`${p.description}; bones: ${p.allowed_bones.join(", ")}`}>{p.name}</span>)}</div>
    </div>
  );
}
