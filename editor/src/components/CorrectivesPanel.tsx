import { useEffect, useState } from "react";
import { api, Payload } from "../api";

// Phase 3.4 corrective artwork: the detected requirements, the generated (or artist-drawn) corrective assets with
// their provenance and approval state, a before/after preview in the current pose, and per-asset settings
// (enabled, blend strength, activation range). Approval is a user action (approval.set on corrective:<id>@v<N>);
// nothing is approved automatically and nothing has to be accepted to keep editing.
export function CorrectivesPanel({ payload, rev, preset, phase, onOp, onError, canvasBusy, canvasResult }: {
  payload: Payload; rev: string; preset: string; phase: number; onOp: (op: Record<string, unknown>) => void;
  onError: (e: string) => void; canvasBusy: boolean; canvasResult: any }) {
  const [info, setInfo] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const [cmp, setCmp] = useState<any>(null);
  const [cmpMode, setCmpMode] = useState<"auto" | "all">("all");
  const load = () => api.correctives().then(setInfo).catch((e) => onError(String(e.message ?? e)));
  useEffect(() => { load(); }, [rev]);
  if (!info) return <div className="panel" data-testid="correctives-panel">Loading corrective artwork…</div>;
  const assets = info.assets as any[];
  const cur = assets.find((a) => a.asset_id === sel) ?? null;
  const settings = (info.settings ?? {}) as Record<string, any>;
  const s = cur ? settings[cur.asset_id] ?? {} : {};
  const approve = (status: string) => cur && onOp({ type: "approval.set", item: cur.approval_key, status, note: "Correctives tab" });
  const generate = () => {
    setBusy("generating (about a minute)…");
    api.correctivesGenerate().then(() => { setBusy(""); load(); }).catch((e) => { setBusy(""); onError(String(e.message ?? e)); });
  };
  const preview = () => {
    setBusy("rendering before/after…");
    const p = preset === "rest" ? "relaxed_standing" : preset;
    api.pose({ preset: p, phase, corrective_mode: cmpMode, corrective_compare: true, allow_pending: cmpMode === "all" })
      .then((r) => { setCmp({ after: r.image, before: r.corrective_before, report: r.correctives, px: r.corrective_px, preset: p }); setBusy(""); })
      .catch((e) => { setBusy(""); onError(String(e.message ?? e)); });
  };
  const upload = (file: File) => {
    const fr = new FileReader();
    fr.onload = () => {
      setBusy("storing your drawing…");
      api.correctivesReplace(cur.asset_id, String(fr.result), "editor upload")
        .then((r) => { setBusy(""); if (!r.ok) onError(r.body.detail ?? "replacement refused"); load(); })
        .catch((e) => { setBusy(""); onError(String(e.message ?? e)); });
    };
    fr.readAsDataURL(file);
  };
  return (
    <div className="panel" data-testid="correctives-panel">
      <div className="panel-title">Corrective artwork</div>
      <p className="hint">{info.note}</p>
      <div className="row small" data-testid="correctives-canvas">canvas ({preset}): {canvasBusy || !canvasResult?.image ? "rendering…" :
        `ready · ${(canvasResult.correctives ?? []).filter((c: any) => c.shown).map((c: any) => `${c.asset_id} ${(+c.weight).toFixed(2)}`).join(", ") || "no corrective shown"}`}</div>
      <label className="row">Show
        <select value={info.mode} onChange={(e) => onOp({ type: "correctives.mode", mode: e.target.value })} data-testid="correctives-mode">
          <option value="auto">approved only (pending with “allow pending”)</option>
          <option value="all">preview all (pending included)</option>
          <option value="off">off (garment rig alone)</option></select></label>
      <div className="row">
        <button onClick={generate} disabled={!!busy} data-testid="correctives-generate">{assets.length ? "Regenerate (new versions)" : "Generate"}</button>
        <button onClick={preview} disabled={!!busy} data-testid="correctives-preview">Before / after in this pose</button>
        <select value={cmpMode} onChange={(e) => setCmpMode(e.target.value as "auto" | "all")} data-testid="correctives-cmp-mode">
          <option value="all">pending preview</option><option value="auto">approved only</option></select>
      </div>
      {busy && <div className="row small" data-testid="correctives-busy">{busy}</div>}
      {cmp && <div className="cmp-pair" data-testid="correctives-compare">
        <figure><img src={cmp.before} alt="without correctives" style={{ width: "100%" }} /><figcaption>without</figcaption></figure>
        <figure><img src={cmp.after} alt="with correctives" style={{ width: "100%" }} /><figcaption>with ({cmp.px} px corrective)</figcaption></figure>
        <div className="small" data-testid="correctives-active">active: {(cmp.report ?? []).filter((c: any) => c.shown).map((c: any) => `${c.asset_id} ${(+c.weight).toFixed(2)} (${c.status})`).join(", ") || "none"}</div>
      </div>}
      <div className="panel-title">Assets ({assets.length}) {info.store_ok ? "" : "· store hash mismatch!"}</div>
      <table className="small" data-testid="correctives-table"><tbody>
        {assets.map((a) => (
          <tr key={a.asset_id} className={sel === a.asset_id ? "sel" : ""} onClick={() => setSel(a.asset_id)} data-testid={`corrective-${a.asset_id}`}>
            <td>{a.category}</td><td>{a.asset_id}@v{a.version}</td><td>{a.provenance}</td>
            <td data-testid={`corrective-state-${a.asset_id}`}>{a.state}</td>
            <td>{settings[a.asset_id]?.enabled === false ? "off" : ""}</td></tr>))}
      </tbody></table>
      {!assets.length && <p className="hint">No corrective assets yet: “Generate” builds them from the character's own pixels (pending review).</p>}
      {cur && <div className="explain" data-testid="corrective-detail">
        <div className="panel-title">{cur.asset_id} v{cur.version}</div>
        <div className="small">{cur.method} · {cur.provenance} · {cur.procedural ? "procedural (shape follows the pose)" : "image"} · placement {cur.placement.mode} · drivers {cur.drivers.join(", ") || "—"}</div>
        <div className="small">activation: {JSON.stringify(cur.activation)}</div>
        <div className="small">Moho: {cur.moho?.class} — {cur.moho?.mapping} ({cur.moho?.status})</div>
        <div className="thumbs">{cur.images.map((im: any) => <figure key={im.key}><img src={api.correctiveImageUrl(im.file, rev)} alt={im.key} style={{ maxWidth: 120, maxHeight: 120, background: "#ddd" }} /><figcaption className="small">{im.key}</figcaption></figure>)}</div>
        <div className="row">
          <button onClick={() => approve("approved")} data-testid="corrective-approve">Approve v{cur.version}</button>
          <button onClick={() => approve("rejected")} data-testid="corrective-reject">Reject</button>
          <button onClick={() => approve("needs_review")}>Back to review</button>
        </div>
        <label className="check"><input type="checkbox" checked={s.enabled !== false} data-testid="corrective-enabled"
          onChange={(e) => onOp({ type: "correctives.set", asset: cur.asset_id, enabled: e.target.checked })} /> enabled</label>
        <label className="row">Blend strength {(s.strength ?? 1).toFixed(2)}<input type="range" min={0} max={1} step={0.05} value={s.strength ?? 1}
          data-testid="corrective-strength" onChange={(e) => onOp({ type: "correctives.set", asset: cur.asset_id, strength: +e.target.value })} /></label>
        {(cur.activation.kind === "ramp" || cur.activation.kind === "keyed") && <div className="row">
          Activation {cur.activation.driver}
          <input type="number" value={s.lo ?? cur.activation.lo} step={1} style={{ width: 60 }} data-testid="corrective-lo"
            onChange={(e) => onOp({ type: "correctives.set", asset: cur.asset_id, lo: +e.target.value, hi: s.hi ?? cur.activation.hi })} />
          → <input type="number" value={s.hi ?? cur.activation.hi} step={1} style={{ width: 60 }} data-testid="corrective-hi"
            onChange={(e) => onOp({ type: "correctives.set", asset: cur.asset_id, lo: s.lo ?? cur.activation.lo, hi: +e.target.value })} /> deg</div>}
        <div className="row">
          <button onClick={() => onOp({ type: "correctives.reset", asset: cur.asset_id })} data-testid="corrective-reset">Reset settings</button>
          {cur.replaceable && <label className="button">Replace with my drawing (PNG, same size)
            <input type="file" accept="image/png" style={{ display: "none" }} data-testid="corrective-upload"
              onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} /></label>}
        </div>
        <div className="small">versions: {cur.versions.map((v: any) => `v${v.version} ${v.provenance} (${v.state})`).join(" · ")}</div>
        <div className="small">Mask and vector editing of a corrective drawing is not implemented: replace the drawing instead.</div>
      </div>}
      <div className="panel-title">Requirements ({info.requirements.length})</div>
      <ul className="small" data-testid="correctives-requirements">
        {info.requirements.map((r: any) => <li key={r.id}><b>{r.id}</b> {r.correction_type} · {r.triggering_poses.join(", ")} · {r.decision}
          {r.assets?.length ? ` → ${r.assets.map((x: any) => `${x.asset_id}@v${x.version} (${x.state})`).join(", ")}` : r.state ? ` → ${r.state}` : ""}</li>)}
      </ul>
      {Object.keys(info.quality).length > 0 && <div className="small" data-testid="correctives-quality">automatic grades (evidence run, not an approval): {Object.entries(info.quality).map(([k, v]: any) => `${k} ${v.status}`).join(" · ")}</div>}
    </div>
  );
}
