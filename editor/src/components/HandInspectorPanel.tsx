import { useEffect, useState } from "react";
import { api, Payload } from "../api";

// Phase 3.7 Hand Selection Inspector. Shows, per side, the drawing the selection engine (or an explicit choice) put on
// the current pose or performance frame: its metadata (handedness, palm view, tolerance), the palm analysis in this
// pose, the wrist checks, every rejected candidate with the hard constraint it failed, and missing artwork. Manual
// override is a switch.set op; metadata corrections are handmeta.* ops. Nothing here approves a drawing.
const POSES = ["open_palm", "relaxed", "fist", "pointing", "thumbs_up", "gripping", "palm_down", "cupped"];
const FIELDS = ["handedness", "gesture", "visible_surface", "rotation_tolerance_deg", "view_elevation", "excluded", "note"];

export function HandInspectorPanel({ payload, rev, onOp, onError }: {
  payload: Payload; rev: string; onOp: (op: Record<string, unknown>) => void; onError: (e: string) => void }) {
  const [mode, setMode] = useState<"review" | "production">("review");
  const [source, setSource] = useState<"pose" | "performance">("pose");
  const [t, setT] = useState(4.4);
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [side, setSide] = useState<"r" | "l">("r");
  const [meta, setMeta] = useState<any>(null);
  const [key, setKey] = useState("");
  const [field, setField] = useState("handedness");
  const [value, setValue] = useState("");
  const [cmp, setCmp] = useState<string | null>(null);
  const hasPerf = !!(payload.state as any).performance?.doc;

  const inspect = (render = true) => {
    setBusy(true);
    api.handsInspect({ mode, source, t, render }).then((d) => { setData(d); setBusy(false); })
      .catch((e) => { onError(String(e.message ?? e)); setBusy(false); });
  };
  useEffect(() => { inspect(false); }, [rev, mode, source]);
  useEffect(() => { api.handsMetadata().then(setMeta).catch(() => setMeta(null)); }, [rev]);

  const S = data?.sides?.[side];
  const parseValue = (f: string, v: string): unknown => {
    if (f === "excluded") return v === "true";
    if (f === "rotation_tolerance_deg") return Number(v);
    return v;
  };
  return (
    <div className="panel hand-inspector" data-testid="hand-inspector">
      <h3>Hand Selection Inspector</h3>
      <div className="row">
        <label>mode <select value={mode} onChange={(e) => setMode(e.target.value as any)} data-testid="hi-mode">
          <option value="review">review (pending previewed, labelled)</option>
          <option value="production">production (approved + original only)</option></select></label>
        <label>source <select value={source} onChange={(e) => setSource(e.target.value as any)} data-testid="hi-source">
          <option value="pose">current pose</option>
          <option value="performance" disabled={!hasPerf}>performance frame</option></select></label>
        {source === "performance" && <label>t <input type="number" step={0.05} value={t} style={{ width: 70 }}
          onChange={(e) => setT(Number(e.target.value))} /></label>}
        <button onClick={() => inspect(true)} disabled={busy} data-testid="hi-inspect">{busy ? "…" : "Inspect (render)"}</button>
      </div>
      <div className="row">
        {(["r", "l"] as const).map((s) => <button key={s} className={side === s ? "on" : ""} onClick={() => setSide(s)}
          data-testid={`hi-side-${s}`}>{s === "r" ? "character right" : "character left"}</button>)}
      </div>
      {S && <div className="hi-body">
        <table className="kv"><tbody>
          <tr><th>active</th><td data-testid="hi-active">{S.active.pose} <code>{S.active.cid}</code> ({S.active.source})
            {S.active.pending_review && <span className="warn"> PENDING REVIEW (preview only)</span>}</td></tr>
          <tr><th>approval</th><td>{S.active.approval ?? "-"}</td></tr>
          <tr><th>handedness</th><td>{S.metadata?.handedness ?? "unknown"} ({S.metadata?.handedness_confidence ?? "-"})
            {S.metadata?.reassigned && <span className="warn"> reassigned from the {S.metadata.store_side === "r" ? "right" : "left"} side</span>}</td></tr>
          <tr><th>shows / label</th><td>{S.metadata?.gesture ?? "-"} / {S.metadata?.label_pose ?? "-"}</td></tr>
          <tr><th>palm in this pose</th><td data-testid="hi-palm">{S.palm?.palm_class ?? "-"}
            {S.palm?.rotation_from_drawn_deg != null && <> · turned {Math.abs(S.palm.rotation_from_drawn_deg).toFixed(0)}° from drawn (tolerance {S.palm.tolerance_deg ?? "-"}°)</>}</td></tr>
          <tr><th>wrist pivot</th><td>{S.wrist_pivot?.map((x: number) => x.toFixed(1)).join(", ")} · offset {JSON.stringify(S.active.offset ?? {})}</td></tr>
          <tr><th>wrist checks</th><td>{S.wrist ? `${S.wrist.status}${S.wrist.failed?.length ? ` (${S.wrist.failed.join(", ")})` : ""}${S.wrist.note ? ` — ${S.wrist.note}` : ""}` : "render to check"}</td></tr>
          {S.override_check && !S.override_check.ok && <tr><th>explicit choice</th><td className="warn">
            {S.override_check.violations.map((v: any) => v.reason).join("; ")}</td></tr>}
        </tbody></table>
        <h4>Explanation</h4>
        <pre className="explain" data-testid="hi-explanation">{(S.decision?.explanation ?? []).join("\n") || S.decision?.reason}</pre>
        {(S.decision?.needs_artwork ?? []).length > 0 && <div className="warn" data-testid="hi-needs">
          {S.decision.needs_artwork.map((n: any) => `${n.status}: ${n.pose}`).join(" · ")}</div>}
        <h4>Rejected candidates</h4>
        <ul className="rejected" data-testid="hi-rejected">
          {(S.decision?.rejected ?? []).map((r: any) => <li key={r.pose + r.cid}><b>{r.pose}</b> {r.cid ? <code>{r.cid}</code> : "(missing)"}: {r.violations.map((v: any) => v.reason).join("; ")}</li>)}
        </ul>
        <h4>Manual override</h4>
        <div className="row">
          {POSES.map((p) => <button key={p} onClick={() => onOp({ type: "switch.set", group: `hand_${side}`, variant: p, auto: false })}
            data-testid={`hi-override-${p}`}>{p}</button>)}
          <button onClick={() => onOp({ type: "switch.set", group: `hand_${side}`, variant: null, auto: true })} data-testid="hi-auto">automatic</button>
        </div>
        <h4>Preview (wrist close-up)</h4>
        <div className="row">
          <img alt="selected" src={api.handsCloseupUrl(side, mode, null, rev)} style={{ maxWidth: 200 }} data-testid="hi-closeup" />
          <select value={cmp ?? ""} onChange={(e) => setCmp(e.target.value || null)}>
            <option value="">compare with…</option>{POSES.map((p) => <option key={p} value={p}>{p}</option>)}</select>
          {cmp && <img alt="compare" src={api.handsCloseupUrl(side, mode, cmp, rev)} style={{ maxWidth: 200 }} />}
        </div>
      </div>}
      <h4>Correct a drawing's metadata</h4>
      {meta?.available ? <div className="row">
        <select value={key} onChange={(e) => setKey(e.target.value)} data-testid="hi-meta-key">
          <option value="">drawing…</option>
          {Object.keys(meta.assets).sort().map((k) => <option key={k} value={k}>{k}</option>)}</select>
        <select value={field} onChange={(e) => setField(e.target.value)}>{FIELDS.map((f) => <option key={f}>{f}</option>)}</select>
        <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="value" style={{ width: 90 }} data-testid="hi-meta-value" />
        <button disabled={!key || value === ""} onClick={() => onOp({ type: "handmeta.set", key, field, value: parseValue(field, value) })}
          data-testid="hi-meta-set">Set</button>
        <button disabled={!key} onClick={() => onOp({ type: "handmeta.reset", key })}>Reset</button>
        {key && <div className="small">current: {JSON.stringify({ hand: meta.assets[key]?.handedness, shows: meta.assets[key]?.gesture,
          tol: meta.assets[key]?.rotation_tolerance_deg, flags: meta.assets[key]?.flags })}</div>}
      </div> : <div className="small">No hand metadata yet: run <code>autorig hand-audit</code>.</div>}
      <p className="small">Scores rank drawings after the hard constraints; they do not measure visual quality. Approval is
        only through the review buttons (approval.set).</p>
    </div>
  );
}
