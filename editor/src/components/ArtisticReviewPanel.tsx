import { useEffect, useState } from "react";
import { api, Payload } from "../api";

// Phase 3.5 Artistic Review: compare pose candidates (clean render + silhouette), see the artwork each uses (hand
// drawings, correctives, layer-order changes), approve or reject poses, choose one, save templates, give feedback
// that becomes structured constraints, and mark motion cycles for revision. Rankings are recommendations only.
export function ArtisticReviewPanel({ payload, rev, onOp, onError }: {
  payload: Payload; rev: string; onOp: (op: Record<string, unknown>) => void; onError: (e: string) => void }) {
  const [info, setInfo] = useState<any>(null);
  const [action, setAction] = useState("point_right");
  const [cands, setCands] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [view, setView] = useState<"render" | "silhouette">("render");
  const [text, setText] = useState("");
  const [parsed, setParsed] = useState<any>(null);
  const [showRef, setShowRef] = useState(false);
  const [cycle, setCycle] = useState("walk");
  const [cycleNote, setCycleNote] = useState("");
  useEffect(() => { api.artistic().then(setInfo).catch((e) => onError(String(e.message ?? e))); }, [rev]);
  if (!info) return <div className="panel" data-testid="artistic-panel">Loading artistic review…</div>;
  const render = () => {
    setBusy("rendering candidates (a few seconds each)…");
    api.artisticCandidates(action).then((r) => { setCands(r); setBusy(""); })
      .catch((e) => { setBusy(""); onError(String(e.message ?? e)); });
  };
  const sel = info.selections?.[action];
  const constraints = (info.constraints ?? []) as any[];
  return (
    <div className="panel" data-testid="artistic-panel">
      <div className="panel-title">Artistic review</div>
      <p className="hint">{info.note}</p>
      <label className="row">Action
        <select value={action} onChange={(e) => { setAction(e.target.value); setCands(null); }} data-testid="artistic-action">
          {Object.entries(info.actions).map(([k, v]: any) => <option key={k} value={k}>{v.title}</option>)}</select></label>
      <div className="small">{info.actions[action]?.intention}</div>
      {info.actions[action]?.requires?.length > 0 && <div className="small warn">needs: {info.actions[action].requires.join("; ")}</div>}
      <div className="row">
        <button onClick={render} disabled={!!busy} data-testid="artistic-render">Render candidates</button>
        <button onClick={() => setView(view === "render" ? "silhouette" : "render")} data-testid="artistic-view">{view === "render" ? "Silhouettes" : "Renders"}</button>
        <button onClick={() => setShowRef(!showRef)}>{showRef ? "Hide" : "Show"} reference</button>
      </div>
      {busy && <div className="small" data-testid="artistic-busy">{busy}</div>}
      {showRef && <img src={`/api/source.png?rev=${rev}`} alt="original reference" style={{ width: "100%" }} />}
      {sel && <div className="small" data-testid="artistic-selected">selected: {sel.variant ?? `template ${sel.template}`}</div>}
      {cands && <div className="cand-grid" data-testid="artistic-candidates" style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {cands.candidates.map((c: any) => (
          <div key={c.variant} className="cand" style={{ width: 160, boxSizing: "border-box", overflowWrap: "anywhere",
            border: c.variant === cands.recommended ? "2px solid #4a90d9" : "1px solid #555", padding: 3 }}
            data-testid={`cand-${c.variant}`}>
            <img src={view === "render" ? c.image : c.silhouette} alt={c.variant} style={{ width: "100%", background: "#fff" }} />
            <div className="small"><b>{c.variant}</b>{c.variant === cands.recommended ? " (recommended)" : ""}</div>
            <div className="small">{c.feasibility.class}</div>
            {c.pending_hands.length > 0 && <div className="small warn">pending hands: {c.pending_hands.join(",")}</div>}
            <div className="small">{c.warnings.length ? c.warnings.map((w: any) => w.code).join(", ") : "no silhouette warning"}</div>
            {c.layer_order?.fired?.length > 0 && <div className="small">order: {c.layer_order.fired.map((f: any) => f.rule).join(", ")}</div>}
            <div className="small" data-testid={`cand-state-${c.variant}`}>{c.state}</div>
            <div className="row" style={{ flexWrap: "wrap", gap: 2 }}>
              <button onClick={() => onOp({ type: "artistic.select", action, variant: c.variant.startsWith("template:") ? undefined : c.variant,
                template: c.variant.startsWith("template:") ? c.variant.slice(9) : undefined })} data-testid={`cand-use-${c.variant}`}>Use</button>
              <button onClick={() => onOp({ type: "approval.set", item: c.approval_key, status: "approved", note: "Artistic Review" })} data-testid={`cand-approve-${c.variant}`}>Approve</button>
              <button onClick={() => onOp({ type: "approval.set", item: c.approval_key, status: "rejected", note: "Artistic Review" })}>Reject</button>
              <button onClick={() => { const n = window.prompt("Template name", `${action}_${c.variant}`); if (n) onOp({ type: "artistic.template.save", name: n, pose: c.pose }); }}
                data-testid={`cand-template-${c.variant}`}>Save template</button>
            </div>
          </div>))}
      </div>}
      <div className="panel-title">Feedback</div>
      <div className="row">
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Move the hand farther from the torso" style={{ flex: 1 }} data-testid="artistic-feedback" />
        <button onClick={() => api.artisticParse(text, action).then(setParsed)} data-testid="artistic-interpret">Interpret</button>
      </div>
      {parsed && <div className="explain" data-testid="artistic-parsed">
        {parsed.understood ? parsed.constraints.map((c: any, i: number) => <div key={i} className="small">{c.kind === "corrective" ? `corrective ${c.asset} ${c.param} ${c.delta > 0 ? "+" : ""}${c.delta}` : `${c.param} ${c.delta > 0 ? "+" : ""}${c.delta}`} ({c.action})</div>)
          : <div className="small">{parsed.note}</div>}
        <button onClick={() => {
          for (const c of parsed.constraints.filter((c: any) => c.kind === "corrective")) {
            const cur = (payload.state as any).correctives?.settings?.[c.asset]?.strength ?? 1;
            onOp({ type: "correctives.set", asset: c.asset, strength: Math.max(0, Math.min(1, cur + c.delta)) });
          }
          onOp({ type: "artistic.feedback", action, text, constraints: parsed.constraints.filter((c: any) => c.kind !== "corrective") });
          setParsed(null); setText("");
        }} data-testid="artistic-apply">Apply</button>
      </div>}
      <div className="small" data-testid="artistic-constraints">constraints ({constraints.length}): {constraints.map((c, i) =>
        <span key={i}>{c.action}:{c.param} {c.delta ?? c.value} <a href="#" onClick={(e) => { e.preventDefault(); onOp({ type: "artistic.constraint.remove", index: i }); }}>×</a>; </span>)}</div>
      <div className="panel-title">Templates ({(info.templates ?? []).length})</div>
      <ul className="small">{(info.templates ?? []).map((t: any) => <li key={t.name}>{t.name} ({t.action})
        <a href="#" onClick={(e) => { e.preventDefault(); onOp({ type: "artistic.template.delete", name: t.name }); }}> delete</a></li>)}</ul>
      <div className="panel-title">Motion cycles</div>
      <div className="row">
        <select value={cycle} onChange={(e) => setCycle(e.target.value)}><option value="walk">walk</option><option value="run">run</option>
          <option value="acting_test">acting test</option></select>
        <input value={cycleNote} onChange={(e) => setCycleNote(e.target.value)} placeholder="what to revise" style={{ flex: 1 }} />
        <button onClick={() => { onOp({ type: "artistic.cycle.revise", cycle, note: cycleNote }); setCycleNote(""); }} data-testid="artistic-cycle-revise">Mark for revision</button>
      </div>
      <ul className="small">{(info.cycle_revisions ?? []).map((c: any, i: number) => <li key={i}>{c.cycle}: {c.note} ({c.status})</li>)}</ul>
    </div>
  );
}
