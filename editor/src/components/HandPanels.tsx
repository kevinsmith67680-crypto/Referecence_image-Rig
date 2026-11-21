// Hand Library: candidate hand drawings per side and pose (the original hand, Phase 2 sheet drawings, imported
// drawings from the character's Hand Pose Store). Choosing, approving, aligning and masking are project operations
// (undo/redo, persisted). Imported drawings start pending review and are never activated for final output until a
// person approves them.
import { useEffect, useMemo, useState } from "react";
import { api, Payload } from "../api";

type OnOp = (op: Record<string, unknown>) => void;
const badge = (s: string) => <span className={`badge b-${(s || "").toLowerCase()}`}>{s}</span>;
const readB64 = (f: File) => new Promise<string>((res, rej) => {
  const r = new FileReader(); r.onload = () => res(String(r.result)); r.onerror = () => rej(r.error); r.readAsDataURL(f);
});

export function HandLibraryPanel({ payload, rev, onOp, onError, onPreviewPose, previewStatus }: { payload: Payload; rev: string; onOp: OnOp;
  onError: (e: string) => void; onPreviewPose: (side: string, cid: string) => void; previewStatus: string }) {
  const [info, setInfo] = useState<any>(null);
  const [q, setQ] = useState("");
  const [side, setSide] = useState("all");
  const [pose, setPose] = useState("all");
  const [status, setStatus] = useState("all");
  const [sel, setSel] = useState<string | null>(null);
  const [msg, setMsg] = useState<any>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => { api.handstore().then(setInfo).catch((e) => onError(String(e.message ?? e))); }, [rev, tick]);
  const cands: any[] = info?.candidates ?? [];
  const shown = useMemo(() => cands.filter((c) => (side === "all" || c.side === side) && (pose === "all" || c.pose === pose)
    && (status === "all" || c.status === status) && (!q || `${c.label} ${c.pose} ${c.cid}`.toLowerCase().includes(q.toLowerCase()))), [cands, q, side, pose, status]);
  if (!info) return <div className="muted">Loading the hand library…</div>;
  const key = (c: any) => `${c.side}|${c.cid}`;
  const cur = cands.find((c) => key(c) === sel) ?? null;
  const uid = cur?.cid?.startsWith("store:") ? cur.cid.slice(6) : null;
  const st = payload.state.hand_store ?? { placement: {}, mask: {}, labels: {}, deleted: [], use: {}, versions: {} };
  const pl = (uid && st.placement[uid]) || { dx: 0, dy: 0, rot: 0, scale: 1 };
  const mk = { enabled: true, defringe: true, cut_offset: 0, ...((uid && st.mask[uid]) || {}) };
  const place = (f: Record<string, number>) => uid && onOp({ type: "handstore.placement", uid, fields: { dx: 0, dy: 0, rot: 0, scale: 1, ...pl, ...f } });
  const importPack = async (f: File | undefined) => {
    if (!f) return;
    const r = await api.handstoreImport(f.name, await readB64(f));
    setMsg(r.body); setTick((t) => t + 1);
  };
  const replace = async (f: File | undefined) => {
    if (!f || !uid) return;
    const r = await api.handstoreReplace(uid, f.name, await readB64(f));
    setMsg(r.body); setTick((t) => t + 1);
  };
  return (
    <div className="form" data-testid="hand-library">
      <div className="muted">Hand drawings per side and pose. Imported drawings are AI-generated concepts: they start
        <b> pending review</b>, show only in “preview pending”, and need your approval before they are used.</div>
      <div className="row gap wrap">
        <label className="filebtn">Import hand pack (.zip)<input type="file" accept=".zip" data-testid="hand-import" onChange={(e) => importPack(e.target.files?.[0])} /></label>
        {info.store && <span className="small">{info.store.packs.length} pack import(s) · {Object.keys(info.store.assets).length} stored drawings
          {info.store.verify.length > 0 && <span className="err"> · {info.store.verify.length} files changed on disk!</span>}</span>}
      </div>
      {msg && <div className={msg.status === "rejected" ? "err-box" : "ok-box"} data-testid="hand-import-result">
        {msg.status}{msg.library_id ? ` ${msg.library_id} v${msg.version}` : ""}{msg.errors ? <ul>{msg.errors.map((e: string) => <li key={e}>{e}</li>)}</ul> : null}
        {msg.warnings?.length ? <div>{msg.warnings.join("; ")}</div> : null}</div>}
      <div className="row gap wrap">
        <input placeholder="search" value={q} onChange={(e) => setQ(e.target.value)} data-testid="hand-search" />
        <select value={side} onChange={(e) => setSide(e.target.value)} data-testid="hand-side"><option value="all">both sides</option><option value="r">right</option><option value="l">left</option></select>
        <select value={pose} onChange={(e) => setPose(e.target.value)} data-testid="hand-pose"><option value="all">all poses</option>{info.poses.map((p: string) => <option key={p}>{p}</option>)}</select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}><option value="all">any status</option>{["original", "approved", "pending_review", "rejected"].map((s) => <option key={s}>{s}</option>)}</select>
      </div>
      {info.missing.length > 0 && <div className="warn-box">NEEDS_ARTWORK: {info.missing.map((m: any) => `${m.pose} (${m.side})`).join(", ")} — the reference hand is shown instead.</div>}
      <div className="thumbs" data-testid="hand-grid">
        {shown.map((c) => (
          <div key={key(c)} className={`thumb ${sel === key(c) ? "sel" : ""} ${c.active ? "active" : ""}`} data-testid={`hc-${c.side}-${c.pose}-${c.cid.replace(/[/:]/g, "_")}`}
            onClick={() => setSel(key(c))} title={c.cid}>
            <img src={api.handPreviewUrl(c.side, c.cid, rev)} alt={c.label} />
            <div className="small">{c.side === "r" ? "R" : "L"} · {c.pose}</div>
            <div className="small">{c.label}</div>
            <div>{badge(c.status)} {c.active && <span className="badge">active</span>}</div>
          </div>))}
      </div>
      {cur && <div className="ik-limb" data-testid="hand-detail">
        <div className="row gap"><b>{cur.label}</b> {badge(cur.status)} <span className="small">{cur.side === "r" ? "right" : "left"} {cur.pose} · {cur.category}</span></div>
        <div className="row gap">
          <img src={api.handPreviewUrl(cur.side, cur.cid, rev)} alt="on model" width={180} height={180} />
          {uid && <img src={api.handSourceUrl(uid, st.versions[uid])} alt="source" width={180} style={{ background: "#e9ebef" }} />}
        </div>
        <div className="small">Provenance: {cur.provenance?.artwork_source ?? cur.provenance?.from ?? cur.category}
          {cur.provenance?.sha256 && <> · v{cur.provenance.version} · sha256 {String(cur.provenance.sha256).slice(0, 12)}…</>}</div>
        {cur.alignment && <div className="kv"><b>Wrist</b> {badge(cur.alignment.status ?? "-")} {cur.cid.startsWith("store:")
          ? <>anchor on the cuff edge (residual {cur.alignment.attachment_error_px} px) · wrist ×{cur.alignment.wrist_fit} of the sleeve opening</>
          : <>visible attachment {cur.alignment.attachment_error_px ?? "-"} px from the reference · width ×{cur.alignment.width_ratio ?? "-"}</>} · side {cur.alignment.side_ok ? "ok" : "SWAPPED"}
          {cur.alignment.issues?.map((i: string) => <div key={i} className="warn-box">{i}</div>)}</div>}
        <div className="row gap wrap">
          <button onClick={() => onOp({ type: "handstore.use", side: cur.side, pose: cur.pose, candidate: cur.cid })} disabled={cur.active} data-testid="hand-use">Use for {cur.pose}</button>
          {cur.approval_item && <>
            <button onClick={() => onOp({ type: "approval.set", item: cur.approval_item, status: "approved" })} data-testid="hand-approve">Approve</button>
            <button onClick={() => onOp({ type: "approval.set", item: cur.approval_item, status: "rejected" })} data-testid="hand-reject">Reject</button></>}
          <button onClick={() => onPreviewPose(cur.side, cur.cid)} data-testid="hand-preview-pose">Preview in pose</button>
          <span className="small" data-testid="hand-preview-status">{previewStatus}</span>
          <button onClick={() => { onOp({ type: "switch.set", group: `hand_${cur.side}`, variant: "open_palm", auto: false }); onOp({ type: "handstore.use", side: cur.side, pose: "open_palm", candidate: "original" }); }}
            data-testid="hand-revert">Revert {cur.side === "r" ? "right" : "left"} hand to original</button>
        </div>
        {uid && <>
          <div className="row gap wrap">
            <button onClick={() => { const l = prompt("Name", cur.label); if (l) onOp({ type: "handstore.rename", uid, label: l }); }} data-testid="hand-rename">Rename</button>
            <button onClick={() => onOp({ type: "handstore.delete", uid })} data-testid="hand-delete">Delete candidate</button>
            <label className="filebtn">Replace PNG<input type="file" accept=".png" onChange={(e) => replace(e.target.files?.[0])} /></label>
            {info.store?.assets[uid]?.versions?.length > 1 && <select value={st.versions[uid] ?? ""} onChange={(e) => onOp({ type: "handstore.version", uid, v: e.target.value ? +e.target.value : null })}>
              <option value="">latest version</option>{info.store.assets[uid].versions.map((v: any) => <option key={v.v} value={v.v}>v{v.v} ({v.origin})</option>)}</select>}
          </div>
          <b>Wrist anchor matching</b>
          <div className="small">Automatic: the drawing's own cuff edge goes to the character's shirt-cuff edge (from the skeleton and the original pixels), tucked under the sleeve; one scale per side. The manifest anchor is not used
            {cur.placement?.manifest_anchor_delta_px && <> (it is off by {cur.placement.manifest_anchor_delta_px.join(", ")} sprite px)</>}.</div>
          <div className="row gap wrap" data-testid="hand-placement">
            {([["dx", -1], ["dx", 1], ["dy", -1], ["dy", 1], ["rot", -2], ["rot", 2]] as [string, number][]).map(([k, d]) => (
              <button key={k + d} onClick={() => place({ [k]: (pl as any)[k] + d })} data-testid={`hand-${k}${d > 0 ? "p" : "m"}`}>{k}{d > 0 ? "+" : ""}{d}</button>))}
            <button onClick={() => place({ scale: +(pl.scale * 0.97).toFixed(4) })}>scale −3%</button>
            <button onClick={() => place({ scale: +(pl.scale * 1.03).toFixed(4) })}>scale +3%</button>
            <button onClick={() => onOp({ type: "handstore.placement", uid, fields: null })}>reset</button>
          </div>
          <div className="small">correction dx {pl.dx ?? 0} · dy {pl.dy ?? 0} · rot {pl.rot ?? 0}° · scale ×{pl.scale ?? 1}
            {cur.placement?.final && <> · final scale {cur.placement.final.scale} · angle {cur.placement.final.angle_deg}°</>}</div>
          <b>Sleeve and cuff mask</b>
          <div className="row gap wrap">
            <label className="check"><input type="checkbox" checked={!!mk.enabled} data-testid="hand-mask" onChange={(e) => onOp({ type: "handstore.mask", uid, fields: { enabled: e.target.checked } })} />hide the drawing's own sleeve and cuff</label>
            <label className="check"><input type="checkbox" checked={!!mk.defringe} onChange={(e) => onOp({ type: "handstore.mask", uid, fields: { defringe: e.target.checked } })} />remove grey matte halo</label>
            <span>cut line {mk.cut_offset ?? 0} px</span>
            <button onClick={() => onOp({ type: "handstore.mask", uid, fields: { cut_offset: (mk.cut_offset ?? 0) - 2 } })}>up</button>
            <button onClick={() => onOp({ type: "handstore.mask", uid, fields: { cut_offset: (mk.cut_offset ?? 0) + 2 } })}>down</button>
          </div>
          {cur.placement?.mask && <div className="small">masked {cur.placement.mask.masked_px} px (sleeve {cur.placement.mask.sleeve_px}, cuff {cur.placement.mask.cuff_px}); skin removed {cur.placement.mask.skin_masked_px} px; halo removed {cur.placement.halo_removed_px} px</div>}
        </>}
      </div>}
      {st.deleted.length > 0 && <div className="kv"><b>Deleted candidates</b> {st.deleted.map((u: string) => <span key={u} className="badge">{u.split("/")[1]}
        <button className="x" onClick={() => onOp({ type: "handstore.restore", uid: u })}>restore</button></span>)}</div>}
    </div>
  );
}
