// Phase 3.2 panels: IK (targets, poles, FK/IK with matching, foot locks, pelvis offset) and Switches (hand
// variants with thumbnails, approval, alignment offsets, keys; correction groups; face channel; live actions).
import { useEffect, useState } from "react";
import { api, Payload } from "../api";

type OnOp = (op: Record<string, unknown>) => void;
const badge = (s: string) => <span className={`badge b-${(s || "").toLowerCase()}`}>{s}</span>;
const LIMBS = ["arm_r", "arm_l", "leg_r", "leg_l"] as const;
const LIMB_LABEL: Record<string, string> = { arm_r: "Right arm (wrist)", arm_l: "Left arm (wrist)", leg_r: "Right leg (ankle)", leg_l: "Left leg (ankle)" };
const fmt = (p: number[] | null | undefined) => (p ? `${p[0].toFixed(1)}, ${p[1].toFixed(1)}` : "-");

export interface IKContext { preset: string; phase: number; speed: number; exaggeration: number; allowPending: boolean; fluid: boolean }

export function IKPanel({ payload, result, ctx, busy: rendering, onOp, onError }: { payload: Payload; result: any; ctx: IKContext; busy: boolean; onOp: OnOp; onError: (e: string) => void }) {
  const ik = payload.state.ik;
  const rep = result?.ik;
  const [busy, setBusy] = useState<string | null>(null);
  const match = (limb: string, to: "ik" | "fk") => {
    setBusy(limb);
    api.ikMatch({ limb, to, preset: ctx.preset, phase: ctx.phase, speed: ctx.speed, exaggeration: ctx.exaggeration,
      allow_pending: ctx.allowPending, fluid: ctx.fluid })
      .then((r) => { onOp({ type: "ik.set", limb, fields: r.state }); setBusy(null); })
      .catch((e) => { onError(String(e.message ?? e)); setBusy(null); });
  };
  const set = (limb: string, fields: Record<string, unknown>) => onOp({ type: "ik.set", limb, fields });
  const po = ik.pelvis_offset ?? [0, 0];
  return (
    <div className="form" data-testid="ik-panel">
      <div className="muted">Drag the square handles on the canvas to move wrist and ankle targets, the diamonds to move
        elbow/knee poles. Switching FK/IK matches the current pose (no jump). Limbs keep their segment lengths: an
        unreachable target is clamped and reported.</div>
      {LIMBS.map((limb) => {
        const c = ik[limb] ?? {};
        const r = rep?.limbs?.[limb];
        const leg = limb.startsWith("leg");
        const mode = !c.enabled ? "planner" : c.mode;
        return (
          <div key={limb} className="ik-limb" data-testid={`ik-${limb}`}>
            <div className="row gap"><b>{LIMB_LABEL[limb]}</b>
              {(["planner", "fk", "ik"] as const).map((m) => (
                <button key={m} className={mode === m ? "on" : ""} disabled={busy === limb || (leg && m === "fk")}
                  data-testid={`ik-${limb}-${m}`}
                  title={m === "planner" ? "the preset/gait drives this limb" : m === "fk" ? "rotations (matched to the current pose)" : "end-effector target (matched)"}
                  onClick={() => m === "planner" ? onOp({ type: "ik.reset", limb }) : match(limb, m)}>{m.toUpperCase()}</button>))}
              {r?.status && c.enabled && c.mode === "ik" && badge(r.status === "ok" ? "OK" : r.status)}
            </div>
            {c.enabled && c.mode === "ik" && <>
              <div className="kv"><b>Target</b> {fmt(c.target)} {r && <span className="muted">· error {r.error_px ?? "-"} px · flexion {r.flexion_deg ?? "-"}°</span>}</div>
              <div className="row gap">
                <label>x <input type="number" step={1} defaultValue={c.target?.[0]?.toFixed(1)} key={`${limb}tx${c.target?.[0]}`}
                  onBlur={(e) => +e.target.value !== c.target?.[0] && set(limb, { target: [+e.target.value, c.target[1]] })} /></label>
                <label>y <input type="number" step={1} defaultValue={c.target?.[1]?.toFixed(1)} key={`${limb}ty${c.target?.[1]}`}
                  onBlur={(e) => +e.target.value !== c.target?.[1] && set(limb, { target: [c.target[0], +e.target.value] })} /></label>
              </div>
              <div className="kv"><b>Pole</b> {fmt(c.pole)} {!c.pole && <span className="muted">(anatomical default)</span>}
                {c.pole && <button onClick={() => set(limb, { pole: null })}>default pole</button>}</div>
              {!leg && <div className="row gap">
                <span>Bend side {c.bend ? (c.bend > 0 ? "+" : "−") : "from pole"}</span>
                <button onClick={() => set(limb, { bend: c.bend ? -c.bend : -1 })} data-testid={`ik-${limb}-flip`}>flip elbow</button>
                <label className="check"><input type="checkbox" checked={!!c.torso_follow} onChange={(e) => set(limb, { torso_follow: e.target.checked })} />torso follows</label>
              </div>}
              {leg && <label className="check"><input type="checkbox" checked={!!c.lock} data-testid={`ik-${limb}-lock`}
                onChange={(e) => set(limb, { lock: e.target.checked })} />foot lock (planted on the ground)</label>}
              <label>Max {leg ? "knee" : "elbow"} flexion {c.max_flex ?? "default"}
                <input type="number" min={1} max={179} defaultValue={c.max_flex ?? ""} key={`${limb}mf${c.max_flex}`}
                  onBlur={(e) => set(limb, { max_flex: e.target.value ? +e.target.value : null })} /></label>
              <label>{leg ? "Foot" : "Wrist"} orientation (deg from rest; empty = planner)
                <input type="number" min={-180} max={180} defaultValue={c.end_angle ?? ""} key={`${limb}ea${c.end_angle}`} data-testid={`ik-${limb}-angle`}
                  onBlur={(e) => set(limb, { end_angle: e.target.value === "" ? null : +e.target.value })} /></label>
              {r?.reach_px && <div className="muted small">reach {r.reach_px[0]}–{r.reach_px[1]} px from the {leg ? "hip" : "shoulder"}</div>}
            </>}
            {c.enabled && c.mode === "fk" && c.fk && <div className="kv"><b>FK</b> abduction {c.fk.abduction?.toFixed(1)}° · elbow {c.fk.flexion?.toFixed(1)}°
              <button onClick={() => match(limb, "ik")}>back to IK (matched)</button></div>}
            {rep?.contacts && leg && <div className="kv"><b>Contact</b> {badge(rep.contacts[limb.slice(-1)]?.state ?? "-")}
              {rep.contacts[limb.slice(-1)]?.lock_error_px != null && <span className="muted"> lock error {rep.contacts[limb.slice(-1)].lock_error_px} px</span>}</div>}
          </div>);
      })}
      <div className="ik-limb">
        <b>Pelvis (feet stay locked)</b>
        <label>dx {po[0].toFixed(0)} px <input type="range" min={-60} max={60} step={1} defaultValue={po[0]} key={`pdx${po[0]}`} data-testid="ik-pelvis-dx"
          onMouseUp={(e) => onOp({ type: "ik.set", limb: "pelvis", fields: { pelvis_offset: [+(e.target as HTMLInputElement).value, po[1]] } })} /></label>
        <label>dy {po[1].toFixed(0)} px (+ = lower) <input type="range" min={0} max={80} step={1} defaultValue={po[1]} key={`pdy${po[1]}`}
          onMouseUp={(e) => onOp({ type: "ik.set", limb: "pelvis", fields: { pelvis_offset: [po[0], +(e.target as HTMLInputElement).value] } })} /></label>
      </div>
      <div className="row gap"><button onClick={() => onOp({ type: "ik.reset", limb: "all" })} data-testid="ik-reset">Reset all IK</button></div>
      {rep?.notes?.length > 0 && <div className="warn-box">{rep.notes.map((n: string) => <div key={n}>{n}</div>)}</div>}
      <div className="muted small" data-testid="ik-timing">{rendering ? "rendering… " : ""}{result?.timing_ms ? `solve ${result.timing_ms.solve} ms${result.timing_ms.total ? ` · full frame ${result.timing_ms.total} ms` : ""}` : ""}</div>
    </div>
  );
}

export function SwitchesPanel({ payload, result, rev, phase, onOp, onError }: { payload: Payload; result: any; rev: string; phase: number; onOp: OnOp; onError: (e: string) => void }) {
  const [info, setInfo] = useState<any>(null);
  const [actions, setActions] = useState<Record<string, any> | null>(null);
  useEffect(() => { api.switches().then(setInfo).catch((e) => onError(String(e.message ?? e))); }, [rev]);
  useEffect(() => { api.liveActions().then(setActions).catch(() => {}); }, []);
  if (!info) return <div className="muted">Loading switch groups…</div>;
  const sw = payload.state.switches ?? {};
  const res = result?.switches ?? {};
  const lib = info.hand_library;
  const runAction = (a: string) => api.liveAction({ action: a, value: 1, t: 0 }).then((r) => {
    r.ops.filter((o: any) => o.type !== "live.head").forEach((o: any) => onOp(o));
  }).catch((e) => onError(String(e.message ?? e)));
  return (
    <div className="form" data-testid="switches-panel">
      <div className="muted">One drawing per group is active. Order: your choice &gt; keyframe &gt; automatic preset choice &gt;
        the original. Pending drawings are only previewed (Pose tab: “preview pending”); missing ones are NEEDS_ARTWORK.</div>
      {info.groups.filter((g: any) => g.kind === "hand").map((g: any) => {
        const s = sw[g.id] ?? { user: null, auto: true, keys: [], offsets: {} };
        const r = res[g.id];
        const side = g.id.slice(-1);
        return (
          <div key={g.id} className="ik-limb" data-testid={`sw-${g.id}`}>
            <div className="row gap"><b>{side === "r" ? "Right hand" : "Left hand"}</b>
              <button className={s.auto ? "on" : ""} onClick={() => onOp({ type: "switch.set", group: g.id, variant: null, auto: true })} data-testid={`sw-${g.id}-auto`}>Auto</button>
              {r && <span className="small">active <b data-testid={`sw-${g.id}-active`}>{r.active}</b> ({r.source})</span>}
            </div>
            {r?.note && <div className="warn-box">{r.note}</div>}
            <div className="thumbs">
              {g.variants.map((v: any) => {
                const al = lib.alignment?.[side]?.[v.id];
                const chosen = (!s.auto && s.user === v.id);
                return (
                  <div key={v.id} className={`thumb ${chosen ? "sel" : ""} ${r?.active === v.id ? "active" : ""}`} data-testid={`sw-${g.id}-${v.id}`}
                    onClick={() => onOp({ type: "switch.set", group: g.id, variant: v.id, auto: false })} title={al?.issues?.join("; ") || v.status}>
                    {v.layer ? <img src={api.switchThumbUrl(g.id, v.id, rev)} alt={v.id} /> : <div className="missing">NEEDS ARTWORK</div>}
                    <div className="small">{v.label}</div>
                    <div>{badge(v.status)}</div>
                    {al?.attachment_error_px != null && <div className={`small ${al.status === "OK" ? "ok" : "err"}`}>wrist {al.attachment_error_px} px</div>}
                  </div>);
              })}
            </div>
            {(() => {
              const cur = !s.auto && s.user ? s.user : r?.active;
              const v = g.variants.find((x: any) => x.id === cur);
              if (!v || !v.layer) return null;
              const al = lib.alignment?.[side]?.[cur];
              const off = s.offsets?.[cur] ?? { dx: 0, dy: 0, rot: 0 };
              const drawing = lib.variants?.[side]?.[cur]?.drawing;
              return <>
                {v.status !== "original" && <div className="row gap">Review “{cur}” drawing {badge(v.status)}
                  <button onClick={() => onOp({ type: "approval.set", item: `corrective:hand__${drawing}`, status: "approved" })} data-testid={`sw-${g.id}-approve`}>Approve</button>
                  <button onClick={() => onOp({ type: "approval.set", item: `corrective:hand__${drawing}`, status: "rejected" })}>Reject</button></div>}
                {al && <div className="kv"><b>Alignment</b> {badge(al.status)} wrist {al.attachment_error_px} px · width ×{al.width_ratio} · side {al.side_ok ? "ok" : "SWAPPED"}
                  {al.issues?.map((i: string) => <div key={i} className="warn-box">{i}</div>)}</div>}
                <div className="row gap">offset dx {off.dx.toFixed(1)} dy {off.dy.toFixed(1)} rot {off.rot.toFixed(1)}°
                  {al?.auto_align && cur !== "open_palm" && <button data-testid={`sw-${g.id}-align`}
                    title={`best translation ${al.auto_align.offset_px.join(", ")} px: wrist error ${al.auto_align.error_before_px} -> ${al.auto_align.error_after_px} px`}
                    onClick={() => onOp({ type: "switch.offset", group: g.id, variant: cur, dx: al.auto_align.offset_px[0], dy: al.auto_align.offset_px[1], rot: 0 })}>Auto-align wrist ({al.auto_align.error_before_px} → {al.auto_align.error_after_px} px)</button>}
                  <button onClick={() => onOp({ type: "switch.offset", group: g.id, variant: cur, dx: 0, dy: 0, rot: 0 })}>Reset</button></div>
                <div className="row gap">{([["dx", -1], ["dx", 1], ["dy", -1], ["dy", 1], ["rot", -2], ["rot", 2]] as [string, number][]).map(([k, d]) => (
                  <button key={k + d} onClick={() => onOp({ type: "switch.offset", group: g.id, variant: cur, ...off, [k]: (off as any)[k] + d })}>{k}{d > 0 ? "+" : ""}{d}</button>))}</div>
              </>;
            })()}
            <div className="row gap">
              <button onClick={() => onOp({ type: "switch.key", group: g.id, t: phase, variant: (!s.auto && s.user) || r?.active || "open_palm" })} data-testid={`sw-${g.id}-key`}>Key at phase {phase.toFixed(2)}</button>
              {s.keys.map(([t, v]: [number, string]) => <span key={t} className="badge">{t.toFixed(2)}: {v}
                <button className="x" onClick={() => onOp({ type: "switch.key", group: g.id, t, variant: null })}>×</button></span>)}
            </div>
          </div>);
      })}
      <b>Pose-aware corrections</b>
      <table data-testid="sw-corrections"><thead><tr><th>Correction</th><th>Now</th><th>Asset</th><th>Mode</th></tr></thead><tbody>
        {info.corrections.map((c: any) => {
          const now = (result?.correction_switches ?? []).find((x: any) => x.id === c.id);
          const gs = sw[`corr_${c.id}`];
          return <tr key={c.id}><td title={`trigger ${c.bone} >= ${c.on}° (hysteresis ${c.hysteresis}°)`}>{c.title} ({c.side})</td>
            <td>{now ? `on (${now.value_deg}°)` : "off"}</td><td>{badge(c.asset_state)}</td>
            <td><select value={gs && !gs.auto ? gs.user ?? "auto" : "auto"} onChange={(e) => onOp(e.target.value === "auto"
              ? { type: "switch.set", group: `corr_${c.id}`, variant: null, auto: true }
              : { type: "switch.set", group: `corr_${c.id}`, variant: e.target.value, auto: false })}>
              <option value="auto">auto</option><option value="on">force on</option><option value="off">force off</option></select></td></tr>;
        })}</tbody></table>
      {info.groups.filter((g: any) => g.kind === "face").map((g: any) => (
        <div key={g.id} className="kv"><b>Face channel: {g.id}</b> {g.variants.map((v: any) => <span key={v.id}>{v.id} {badge(v.status)} </span>)}</div>))}
      {actions && <>
        <b>Live actions (semantic, no webcam)</b>
        <div className="row gap wrap" data-testid="live-actions">{Object.entries(actions).filter(([, a]: any) => a.channel === "body").map(([n]) => (
          <button key={n} onClick={() => runAction(n)} data-testid={`act-${n}`}>{n}</button>))}</div>
        <div className="muted small">Moho Live Mode input names are not assigned (not yet discovered); the mapping stays proposed_unverified.</div>
      </>}
    </div>
  );
}
