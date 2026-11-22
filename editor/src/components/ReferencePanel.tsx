import { useEffect, useState } from "react";
import { api, Payload } from "../api";

// Phase 3.7 Reference Animation workspace. The library is character-independent (MotionReferenceStore); the choices made
// here (selected reference, segment, intensity, timing, hand actions, annotations, presets, revision decisions) are
// reference.* project ops. "Use as performance" writes the retargeted timeline with performance.set, so playback,
// curves and rendering are the existing Performance tab. Pose estimation runs locally at import only.
export function ReferencePanel({ payload, rev, onOp, onError }: {
  payload: Payload; rev: string; onOp: (op: Record<string, unknown>) => void; onError: (e: string) => void }) {
  const [info, setInfo] = useState<any>(null);
  const [ref, setRef] = useState<any>(null);
  const [frame, setFrame] = useState(0);
  const [rt, setRt] = useState<any>(null);
  const [crit, setCrit] = useState<any>(null);
  const [rev2, setRev2] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [imp, setImp] = useState({ ref_id: "", source: "", usage_permission: "", gesture: "", hand_r: "", hand_l: "" });
  const [file, setFile] = useState<File | null>(null);
  const [ann, setAnn] = useState("");
  const [preset, setPreset] = useState("");
  const R = (payload.state as any).reference_motion ?? { selected: null, settings: {}, annotations: {}, presets: {} };
  const sel: string | null = R.selected;
  const st = (sel && R.settings[sel]) || {};
  const err = (e: any) => { onError(String(e.message ?? e)); setBusy(""); };

  useEffect(() => { api.reference().then(setInfo).catch(err); }, [rev]);
  useEffect(() => {
    if (!sel) { setRef(null); return; }
    api.referenceGet(sel).then((r) => { setRef(r); setFrame(0); }).catch(err);
    setRt(null); setCrit(null); setRev2(null);
  }, [sel]);
  const body = () => ({ intensity: st.intensity ?? 1, timing_scale: st.timing_scale ?? 1, segment: st.segment ?? null,
    hand_actions: st.hand_actions ?? {} });
  const retarget = () => { setBusy("retarget"); api.referenceRetarget(sel!, body()).then((r) => { setRt(r); setBusy(""); }).catch(err); };
  const critique = () => { setBusy("critique"); api.referenceCritique(sel!, body()).then((r) => { setCrit(r); setBusy(""); }).catch(err); };
  const revise = () => { setBusy("revise"); api.referenceRevise(sel!, body()).then((r) => { setRev2(r); setBusy(""); }).catch(err); };
  const doImport = async () => {
    if (!file) return;
    setBusy("import");
    const b64 = await new Promise<string>((res) => { const fr = new FileReader(); fr.onload = () => res(String(fr.result)); fr.readAsDataURL(file); });
    api.referenceImport({ ...imp, name: file.name, data_b64: b64, hand_actions: { ...(imp.hand_r ? { r: imp.hand_r } : {}), ...(imp.hand_l ? { l: imp.hand_l } : {}) } })
      .then(() => { setBusy(""); api.reference().then(setInfo); }).catch(err);
  };
  const dur = ref ? ref.duration : 0;
  return (
    <div className="panel reference-panel" data-testid="reference-panel">
      <h3>Reference animation</h3>
      <div className="small">Library: <code>{info?.root}</code> · pose model: {info?.providers?.mediapipe} · models: {JSON.stringify(info?.providers?.models ?? {})}</div>
      <ul className="ref-list" data-testid="ref-list">
        {(info?.library ?? []).map((r: any) => <li key={r.id}>
          <button className={sel === r.id ? "on" : ""} onClick={() => onOp({ type: "reference.select", ref_id: r.id })} data-testid={`ref-${r.id}`}>{r.id}</button>
          <span className="small"> {r.tags?.gesture ?? r.category} · {r.tags?.duration_s}s · {r.provenance?.kind}
            {(r.tags?.required_artwork ?? []).length > 0 && <span className="warn"> · needs {r.tags.required_artwork.join(", ")}</span>}</span></li>)}
      </ul>
      {ref && sel && <div className="ref-body">
        <div className="row">
          <img alt="reference skeleton" src={api.referenceFrameUrl(sel, frame)} style={{ width: 210 }} data-testid="ref-frame" />
          <div>
            <input type="range" min={0} max={Math.max(0, ref.frames - 1)} value={frame} onChange={(e) => setFrame(Number(e.target.value))}
              style={{ width: 220 }} data-testid="ref-scrub" />
            <div className="small">t = {(frame / ref.fps).toFixed(2)} / {dur.toFixed(2)} s · {ref.meta.provenance?.kind} ({ref.meta.provenance?.source})</div>
            <div className="small">depth (foreshortened) segments: {(ref.analysis.depth_segments ?? []).join(", ") || "none"}</div>
            <div className="small">holds: {JSON.stringify(ref.timing.holds)} · head lead {ref.analysis.head_lead_s ?? "-"} s</div>
          </div>
        </div>
        <div className="row">
          <label>segment <input type="number" step={0.05} style={{ width: 60 }} value={st.segment?.[0] ?? 0}
            onChange={(e) => onOp({ type: "reference.settings", ref_id: sel, segment: [Number(e.target.value), st.segment?.[1] ?? dur] })} />
            – <input type="number" step={0.05} style={{ width: 60 }} value={st.segment?.[1] ?? dur}
            onChange={(e) => onOp({ type: "reference.settings", ref_id: sel, segment: [st.segment?.[0] ?? 0, Number(e.target.value)] })} /></label>
          <label>intensity <input type="range" min={0} max={2} step={0.05} value={st.intensity ?? 1}
            onChange={(e) => onOp({ type: "reference.settings", ref_id: sel, intensity: Number(e.target.value) })} data-testid="ref-intensity" /> {(st.intensity ?? 1).toFixed(2)}</label>
          <label>timing ×<input type="number" min={0.25} max={4} step={0.05} style={{ width: 60 }} value={st.timing_scale ?? 1}
            onChange={(e) => onOp({ type: "reference.settings", ref_id: sel, timing_scale: Number(e.target.value) })} /></label>
        </div>
        <div className="row">
          {(["r", "l"] as const).map((s) => <label key={s}>hand {s} <select value={st.hand_actions?.[s] ?? ""}
            onChange={(e) => onOp({ type: "reference.settings", ref_id: sel, hand_actions: { ...(st.hand_actions ?? {}), [s]: e.target.value || null } })}>
            <option value="">(from reference)</option>{(info?.actions ?? []).map((a: string) => <option key={a}>{a}</option>)}</select></label>)}
        </div>
        <div className="row">
          <button onClick={retarget} disabled={!!busy} data-testid="ref-retarget">{busy === "retarget" ? "…" : "Retarget"}</button>
          <button onClick={critique} disabled={!!busy} data-testid="ref-critique">{busy === "critique" ? "…" : "Critique (rules)"}</button>
          <button onClick={revise} disabled={!!busy} data-testid="ref-revise">{busy === "revise" ? "…" : "Propose revision"}</button>
        </div>
        {rt && <div data-testid="ref-result">
          <div><b>{rt.feasibility.class}</b> · {rt.keys} keys · {rt.duration.toFixed(2)} s</div>
          {rt.feasibility.items.map((i: any, k: number) => <div key={k} className="warn small">{i.class}: {i.needs} ({i.handling})</div>)}
          <div className="small">angle error rms: {Object.entries(rt.errors).map(([c, e]: any) => `${c} ${e.rms}`).join(" · ")}</div>
          <div className="small">hands: {Object.entries(rt.hand_plan).map(([s, ivs]: any) => `${s}: ` + ivs.map((iv: any) => `${iv.value}${iv.chosen ? `→${iv.chosen}` : ""}`).join(", ")).join(" | ")}</div>
          <button onClick={() => onOp({ type: "performance.set", doc: rt.performance })} data-testid="ref-use">Use as performance (Performance tab)</button>
        </div>}
        {crit && <div data-testid="ref-crit"><h4>Critique (hypotheses; the critic never approves)</h4>
          <ul>{crit.defects.map((d: any, k: number) => <li key={k} className={d.severity === "blocking" ? "warn" : ""}>[{d.severity}] {d.region}: {d.issue} → {d.suggestedCorrection}{d.requiresArtwork ? " (needs artwork)" : ""}</li>)}</ul></div>}
        {rev2 && <div data-testid="ref-rev"><h4>Revision proposal: {rev2.status}</h4>
          <ul>{rev2.attempts.map((a: any, k: number) => <li key={k}>{a.param} {a.step ?? ""}: {a.result} ({a.metric} {a.before} → {a.after})</li>)}</ul>
          {rev2.candidate && <><button onClick={() => { onOp({ type: "performance.set", doc: rev2.candidate }); onOp({ type: "reference.revision", ref_id: sel, decision: "accept", t: rev2.t, attempts: rev2.attempts }); }}
            data-testid="ref-accept">Accept candidate</button>
          <button onClick={() => onOp({ type: "reference.revision", ref_id: sel, decision: "reject", t: rev2.t, attempts: rev2.attempts })}>Reject</button></>}
        </div>}
        <div className="row">
          <input value={ann} onChange={(e) => setAnn(e.target.value)} placeholder="annotation (label)" style={{ width: 160 }} />
          <button disabled={!ann} onClick={() => { onOp({ type: "reference.annotate", ref_id: sel, label: ann, t0: frame / ref.fps }); setAnn(""); }}>Annotate at t</button>
          <input value={preset} onChange={(e) => setPreset(e.target.value)} placeholder="preset name" style={{ width: 120 }} />
          <button disabled={!preset} onClick={() => onOp({ type: "reference.preset.save", ref_id: sel, name: preset })} data-testid="ref-preset">Save preset</button>
        </div>
        <ul className="small">{(R.annotations[sel] ?? []).map((a: any, k: number) => <li key={k}>{a.t0 != null ? `${a.t0.toFixed(2)}s ` : ""}{a.label ?? ""} {a.note ?? ""}</li>)}</ul>
      </div>}
      <h4>Import a reference</h4>
      <div className="row small">
        <input type="file" accept=".mp4,.mov,.gif,.json,.bvh" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        {(["ref_id", "source", "usage_permission", "gesture", "hand_r", "hand_l"] as const).map((k) =>
          <input key={k} placeholder={k} value={(imp as any)[k]} onChange={(e) => setImp({ ...imp, [k]: e.target.value })} style={{ width: 110 }} />)}
        <button disabled={!file || !imp.ref_id || !imp.usage_permission || !imp.source || !!busy} onClick={doImport}>{busy === "import" ? "…" : "Import"}</button>
      </div>
      <p className="small">A usage permission statement is required. Depth motion is reported (REQUIRES_ALTERNATIVE_ARTWORK /
        REQUIRES_NEW_VIEW), never stretched. Nothing here is approved automatically.</p>
    </div>
  );
}
