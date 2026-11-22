import { useEffect, useMemo, useRef, useState } from "react";
import { api, Payload } from "../api";

// Phase 3.6 Performance timeline. Every edit is a performance.* project op (undo/redo, save/reload come from the op
// log). The preview is the fast skeleton-only track (wireframe, scrubbing, playback); "Render frame" and "Render
// animation" use the full artwork pipeline (seconds per frame). Pending hand drawings are previewed and labelled.
const W = 520;
const LANES = ["keys", "UPPER_BODY_GESTURES", "HEAD_MOVEMENT", "ROOT_MOVEMENT", "contacts", "hands"];
const SPEEDS = [0.25, 0.5, 1, 2];

function curvePath(kind: string, params: number[], w: number, h: number): string {
  const f = (u: number): number => {
    if (kind === "linear") return u;
    if (kind === "ease_in") return u * u * u;
    if (kind === "ease_out") return 1 - (1 - u) ** 3;
    if (kind === "ease_in_out_cubic") return u * u * (3 - 2 * u);
    if (kind === "hold") return u >= 1 ? 1 : 0;
    if (kind === "bezier" && params.length === 4) {
      const [x1, y1, x2, y2] = params;
      let lo = 0, hi = 1, s = u;
      for (let i = 0; i < 40; i++) { s = (lo + hi) / 2; const x = 3 * (1 - s) ** 2 * s * x1 + 3 * (1 - s) * s * s * x2 + s ** 3; if (x < u) lo = s; else hi = s; }
      return 3 * (1 - s) ** 2 * s * y1 + 3 * (1 - s) * s * s * y2 + s ** 3;
    }
    return u * u * u * (u * (6 * u - 15) + 10);
  };
  const pts = Array.from({ length: 41 }, (_, i) => i / 40).map((u) => `${(u * w).toFixed(1)},${(h - f(u) * h * 0.8 - h * 0.1).toFixed(1)}`);
  return "M" + pts.join(" L");
}

export function PerformancePanel({ payload, rev, onOp, onError }: {
  payload: Payload; rev: string; onOp: (op: Record<string, unknown>) => void; onError: (e: string) => void }) {
  const [info, setInfo] = useState<any>(null);
  const [track, setTrack] = useState<any>(null);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [channel, setChannel] = useState("arm_r_abduction");
  const [selKey, setSelKey] = useState<string | null>(null);
  const [selClip, setSelClip] = useState<string | null>(null);
  const [gesture, setGesture] = useState("wave");
  const [side, setSide] = useState("r");
  const [markerPose, setMarkerPose] = useState("open_palm");
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const [job, setJob] = useState<any>(null);
  const [saveName, setSaveName] = useState("");
  const raf = useRef<number | null>(null);
  const doc = (payload.state as any).performance?.doc;

  useEffect(() => { api.performance().then(setInfo).catch((e) => onError(String(e.message ?? e))); }, [rev]);
  useEffect(() => {
    if (!doc) { setTrack(null); return; }
    api.performanceTrack().then((tr) => { setTrack(tr); setFrameUrl(null); }).catch((e) => onError(String(e.message ?? e)));
  }, [rev, doc ? JSON.stringify(doc).length : 0]);
  useEffect(() => {  // playback over the fast track
    if (!playing || !track) return;
    let last = performance.now();
    const step = (now: number) => {
      const dt = (now - last) / 1000; last = now;
      setT((cur) => { let n = cur + dt * speed; if (n > track.duration) { if (loop) n = 0; else { setPlaying(false); n = track.duration; } } return n; });
      raf.current = requestAnimationFrame(step);
    };
    raf.current = requestAnimationFrame(step);
    return () => { if (raf.current) cancelAnimationFrame(raf.current); };
  }, [playing, track, speed, loop]);
  useEffect(() => {  // poll a render job
    if (!job || job.done) return;
    const id = setInterval(() => api.cycleStatus(job.id).then((s) => setJob({ ...job, ...s })).catch(() => {}), 1500);
    return () => clearInterval(id);
  }, [job]);

  const fi = track ? Math.min(track.frames.length - 1, Math.max(0, Math.round(t * track.fps))) : 0;
  const frame = track?.frames[fi];
  const x = (tt: number) => (track ? (tt / Math.max(track.duration, 1e-6)) * W : 0);
  const channels = useMemo(() => (track ? Object.keys(track.frames[0].params).sort() : []), [track]);
  const range = useMemo(() => {
    if (!track) return [0, 1];
    const v = track.frames.map((f: any) => f.params[channel] ?? 0);
    const lo = Math.min(...v), hi = Math.max(...v);
    return [lo, hi === lo ? lo + 1 : hi];
  }, [track, channel]);

  if (!info) return <div className="panel" data-testid="performance-panel">Loading performance…</div>;
  const loadScenario = (name: string) => api.performanceScenario(name).then((r) => onOp({ type: "performance.set", doc: r.doc }))
    .catch((e) => onError(String(e.message ?? e)));
  const key = track?.keys.find((k: any) => k.id === selKey);
  const clip = doc?.gestures?.find((g: any) => g.id === selClip);

  // wireframe of the solved skeleton (fast preview)
  const wire = () => {
    if (!frame) return null;
    const pts = Object.values(frame.joints).concat(Object.values(frame.tails)) as number[][];
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs) - 40, x1 = Math.max(...xs) + 40, y0 = Math.min(...ys) - 40, y1 = Math.max(...ys) + 40;
    const desks = (track.props?.desks ?? []) as any[];
    return (
      <svg viewBox={`${x0} ${y0} ${x1 - x0} ${y1 - y0}`} style={{ width: 200, height: 260, background: "#fff" }} data-testid="perf-wire">
        {desks.map((d, i) => <rect key={i} x={d.x0} y={d.top_y} width={d.x1 - d.x0} height={d.depth_px} fill="#c49a6c" />)}
        {track.bones.map(([b]: string[]) => <line key={b} x1={frame.joints[b][0]} y1={frame.joints[b][1]} x2={frame.tails[b][0]} y2={frame.tails[b][1]}
          stroke={b.endsWith("_r") ? "#d9534f" : b.endsWith("_l") ? "#4a90d9" : "#333"} strokeWidth={6} strokeLinecap="round" />)}
      </svg>);
  };

  return (
    <div className="panel" data-testid="performance-panel">
      <div className="panel-title">Performance</div>
      <p className="hint">{info.note}</p>
      <div className="row gap">
        {info.scenarios.map((s: string) => <button key={s} onClick={() => loadScenario(s)} data-testid={`perf-scenario-${s}`}>Load {s}</button>)}
      </div>
      {!doc && <div className="small">No performance yet: load a scenario.</div>}
      {doc && track && <>
        <div className="row gap small">
          <b>{doc.name}</b> {track.duration.toFixed(2)} s · {track.frames.length} frames @ {track.fps} fps · preview {(track.timing.track_s_per_frame * 1000).toFixed(1)} ms/frame
          · feet max step {track.max_foot_step_px.r}/{track.max_foot_step_px.l} px
        </div>
        <svg width="100%" viewBox={`0 0 ${W + 70} ${LANES.length * 16 + 22}`} preserveAspectRatio="none" data-testid="perf-timeline"
          style={{ background: "#1d2128", cursor: "pointer", height: LANES.length * 16 + 22 }}
          onClick={(e) => { const r = (e.target as SVGElement).closest("svg")!.getBoundingClientRect();
            const u = ((e.clientX - r.left) / r.width) * (W + 70) - 70;
            setT(Math.max(0, Math.min(track.duration, (u / W) * track.duration))); }}>
          {LANES.map((l, i) => <text key={l} x={2} y={i * 16 + 30} fill="#8a93a3" fontSize={8}>{l.replace("_GESTURES", "").replace("_MOVEMENT", "").toLowerCase()}</text>)}
          <g transform="translate(70,0)">
            {Array.from({ length: Math.floor(track.duration) + 1 }, (_, s) => <g key={s}><line x1={x(s)} x2={x(s)} y1={0} y2={14} stroke="#556" /><text x={x(s) + 2} y={10} fill="#aab" fontSize={8}>{s}s</text></g>)}
            {track.keys.map((k: any) => <g key={k.id} onClick={(e) => { e.stopPropagation(); setSelKey(k.id); setT(k.t); }} data-testid={`perf-key-${k.id}`}>
              <rect x={x(k.t)} y={20} width={Math.max(1, x(k.t + k.hold) - x(k.t))} height={6} fill="#4a6" opacity={0.6} />
              <polygon points={`${x(k.t)},16 ${x(k.t) + 4},22 ${x(k.t)},28 ${x(k.t) - 4},22`} fill={k.id === selKey ? "#ff0" : "#9c6"} /></g>)}
            {track.clips.map((c: any) => { const lane = LANES.indexOf(c.layer); if (lane < 0) return null;
              const base = (c.id as string).split(":")[0];
              return <rect key={c.id} x={x(c.start)} y={lane * 16 + 18} width={Math.max(2, x(c.end) - x(c.start))} height={10} rx={2}
                fill={base === selClip ? "#e0a030" : "#4a90d9"} opacity={0.8} onClick={(e) => { e.stopPropagation(); setSelClip(base); }}><title>{c.gesture} {c.side ?? ""}</title></rect>; })}
            {track.contacts.map((c: any) => <rect key={c.id} x={x(c.start - c.acquire)} y={4 * 16 + 18} width={Math.max(2, x(c.end + c.release) - x(c.start - c.acquire))} height={10} fill="#c66" opacity={0.8} data-testid="perf-contact-marker"><title>{c.id}</title></rect>)}
            {track.frames.map((f: any, i: number) => i > 0 && (f.hands.r?.pose !== track.frames[i - 1].hands.r?.pose || f.hands.l?.pose !== track.frames[i - 1].hands.l?.pose)
              ? <line key={i} x1={x(f.t)} x2={x(f.t)} y1={5 * 16 + 18} y2={5 * 16 + 28} stroke="#fc4" strokeWidth={2} data-testid="perf-hand-marker" /> : null)}
            <line x1={x(t)} x2={x(t)} y1={0} y2={LANES.length * 16 + 22} stroke="#fff" data-testid="perf-playhead" />
          </g>
        </svg>
        <div className="row gap">
          <button onClick={() => setPlaying(!playing)} data-testid="perf-play">{playing ? "■ Stop" : "▶ Play"}</button>
          <button onClick={() => setT(Math.max(0, (fi - 1) / track.fps))} data-testid="perf-prev">◀ frame</button>
          <button onClick={() => setT(Math.min(track.duration, (fi + 1) / track.fps))} data-testid="perf-next">frame ▶</button>
          <label className="small"><input type="checkbox" checked={loop} onChange={(e) => setLoop(e.target.checked)} /> loop</label>
          <label className="small">speed <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} data-testid="perf-speed">{SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}</select></label>
          <input type="range" min={0} max={track.duration} step={1 / track.fps} value={t} onChange={(e) => setT(Number(e.target.value))} data-testid="perf-scrub" style={{ flex: 1 }} />
          <span className="small" data-testid="perf-time">{t.toFixed(2)} s (frame {fi})</span>
        </div>
        <div className="row gap" style={{ alignItems: "flex-start" }}>
          {wire()}
          <div style={{ flex: 1 }}>
            <div className="small" data-testid="perf-frame-info">hands: r {frame?.hands.r?.pose} ({frame?.hands.r?.status}) · l {frame?.hands.l?.pose} ({frame?.hands.l?.status})
              {Object.keys(frame?.contacts ?? {}).length > 0 && <> · contact w {Object.entries(frame.contacts).map(([s, w]: any) => `${s}:${Number(w).toFixed(2)}`).join(" ")}</>}
              {" · "}hair {frame?.secondary.hair_lag_deg ?? 0}° hem {frame?.secondary.hem_lag_deg ?? 0}°</div>
            <div className="row gap"><button onClick={() => setFrameUrl(api.performanceFrameUrl(t, rev))} data-testid="perf-render-frame">Render frame (full artwork)</button>
              <button onClick={() => api.performanceRender().then((r) => setJob({ ...r, done: false, progress: 0 })).catch((e) => onError(String(e.message ?? e)))} data-testid="perf-render-anim">Render animation</button>
              {job && <span className="small">{job.done ? (job.error ? `error: ${job.error}` : `done (${job.progress} frames)`) : `rendering ${job.progress ?? 0}/${job.frames}`}</span>}</div>
            {frameUrl && <img src={frameUrl} alt="frame" style={{ maxWidth: 220, background: "#fff" }} data-testid="perf-frame-img" />}
          </div>
        </div>
        <div className="panel-title">Curves</div>
        <div className="row gap">
          <select value={channel} onChange={(e) => setChannel(e.target.value)} data-testid="perf-channel">{channels.map((c) => <option key={c}>{c}</option>)}</select>
          <span className="small">{range[0].toFixed(2)} … {range[1].toFixed(2)}</span>
        </div>
        <svg width="100%" viewBox={`0 0 ${W} 90`} preserveAspectRatio="none" style={{ background: "#1d2128", height: 90 }} data-testid="perf-curve">
          <polyline fill="none" stroke="#9cf" strokeWidth={1.5} points={track.frames.map((f: any) => `${x(f.t)},${85 - ((f.params[channel] ?? 0) - range[0]) / (range[1] - range[0]) * 80}`).join(" ")} />
          {track.keys.map((k: any) => <line key={k.id} x1={x(k.t)} x2={x(k.t)} y1={0} y2={90} stroke="#454" />)}
          <line x1={x(t)} x2={x(t)} y1={0} y2={90} stroke="#fff" />
        </svg>
        {key && <div className="explain" data-testid="perf-key-editor">
          <div className="small"><b>key {key.label}</b> at {key.t}s · hold {key.hold}s · anticipation {key.anticipation ?? "none"} · overshoot {key.overshoot}</div>
          <div className="row gap">
            curve <select value={key.curve.kind} onChange={(e) => onOp({ type: "performance.curve.set", key_id: key.id, curve: e.target.value === "bezier" ? { kind: "bezier", params: [0.3, 0, 0.7, 1] } : { kind: e.target.value } })} data-testid="perf-curve-kind">
              {info.curve_kinds.filter((k: string) => k !== "custom").map((k: string) => <option key={k}>{k}</option>)}</select>
            <svg width={60} height={40} style={{ background: "#222" }}><path d={curvePath(key.curve.kind, key.curve.params, 60, 40)} stroke="#fc6" fill="none" /></svg>
            {key.curve.kind === "bezier" && [0, 1, 2, 3].map((i) => <input key={i} type="number" step={0.05} style={{ width: 48 }} defaultValue={key.curve.params[i]}
              onBlur={(e) => { const p = [...key.curve.params]; p[i] = Number(e.target.value); onOp({ type: "performance.curve.set", key_id: key.id, curve: { kind: "bezier", params: p } }); }} />)}
          </div>
          <div className="row gap">
            anticipation <select value={key.anticipation ?? "none"} onChange={(e) => { const src = doc.base_keys.find((k: any) => k.id === key.id); onOp({ type: "performance.key.set", key: { ...src, anticipation: e.target.value } }); }} data-testid="perf-key-anticipation">
              {info.recipes.map((r: string) => <option key={r}>{r}</option>)}</select>
            overshoot <input type="number" step={0.01} min={0} max={0.15} style={{ width: 56 }} defaultValue={key.overshoot}
              onBlur={(e) => { const src = doc.base_keys.find((k: any) => k.id === key.id); onOp({ type: "performance.key.set", key: { ...src, overshoot: Number(e.target.value) } }); }} data-testid="perf-key-overshoot" />
          </div>
        </div>}
        <div className="panel-title">Gesture clips</div>
        <div className="row gap">
          <select value={gesture} onChange={(e) => setGesture(e.target.value)} data-testid="perf-gesture">{Object.entries(info.gestures).map(([n, g]: any) => <option key={n} value={n}>{g.title} [{g.status.status}]</option>)}</select>
          <select value={side} onChange={(e) => setSide(e.target.value)}><option value="r">right</option><option value="l">left</option></select>
          <button onClick={() => onOp({ type: "performance.gesture.add", gesture: { id: `${gesture}_${Date.now() % 100000}`, gesture, start: Number(t.toFixed(2)), side } })} data-testid="perf-gesture-add">Add at playhead</button>
        </div>
        {(doc.gestures ?? []).map((g: any) => <div key={g.id} className="row gap small" style={{ background: g.id === selClip ? "#3a3320" : undefined }}>
          <a href="#" onClick={(e) => { e.preventDefault(); setSelClip(g.id); setT(g.start); }}>{g.gesture} ({g.side}) @ {g.start}s</a>
          <span className={track.gesture_status[g.id]?.status === "SUPPORTED" ? "" : "warn"}>{track.gesture_status[g.id]?.status} {(track.gesture_status[g.id]?.pending ?? []).concat(track.gesture_status[g.id]?.missing ?? []).join(", ")}</span>
          <a href="#" onClick={(e) => { e.preventDefault(); onOp({ type: "performance.gesture.remove", id: g.id }); }}>remove</a></div>)}
        {clip && <div className="row gap small" data-testid="perf-clip-editor">
          {["duration", "anticipation", "overshoot", "amplitude"].map((f) => <label key={f}>{f} <input type="number" step={0.05} style={{ width: 52 }} defaultValue={clip[f] ?? (f === "duration" ? info.gestures[clip.gesture].duration : 1)}
            onBlur={(e) => onOp({ type: "performance.gesture.update", id: clip.id, fields: { [f]: Number(e.target.value) } })} data-testid={`perf-clip-${f}`} /></label>)}
        </div>}
        <div className="panel-title">Style, secondary motion, layers</div>
        <div className="row gap">
          style <select value={doc.style} onChange={(e) => onOp({ type: "performance.style", style: e.target.value })} data-testid="perf-style">{Object.keys(info.styles).map((s) => <option key={s}>{s}</option>)}</select>
        </div>
        {["hair", "hem"].map((p) => { const s = { ...info.secondary_defaults[p], ...(doc.secondary?.[p] ?? {}) };
          return <div key={p} className="row gap small" data-testid={`perf-secondary-${p}`}>
            <label><input type="checkbox" checked={!!s.enabled} onChange={(e) => onOp({ type: "performance.secondary", part: p, settings: { enabled: e.target.checked } })} /> {p}</label>
            {["freq_hz", "damping", "gain", "limit_deg"].map((f) => <label key={f}>{f} <input type="number" step={0.1} style={{ width: 48 }} defaultValue={s[f]}
              onBlur={(e) => onOp({ type: "performance.secondary", part: p, settings: { [f]: Number(e.target.value) } })} /></label>)}
          </div>; })}
        <div className="small warn">tie: {info.secondary_defaults.tie.reason}</div>
        <div className="row gap small" data-testid="perf-layers">{info.layers.map((l: string) => { const s = { mute: false, weight: 1, ...(doc.layers?.[l] ?? {}) };
          return <label key={l}><input type="checkbox" checked={!s.mute} onChange={(e) => onOp({ type: "performance.layer", layer: l, mute: !e.target.checked })} data-testid={`perf-layer-${l}`} />{l.toLowerCase()}</label>; })}</div>
        <div className="panel-title">Hand switch markers</div>
        <div className="row gap">
          <select value={side} onChange={(e) => setSide(e.target.value)}><option value="r">right</option><option value="l">left</option></select>
          <select value={markerPose} onChange={(e) => setMarkerPose(e.target.value)}>{["open_palm", "relaxed", "pointing", "palm_down", "cupped", "fist", "thumbs_up", "gripping"].map((p) => <option key={p}>{p}</option>)}</select>
          <button onClick={() => onOp({ type: "performance.marker.add", marker: { t: Number(t.toFixed(3)), side, action: markerPose } })} data-testid="perf-marker-add">Add marker at playhead</button>
        </div>
        <ul className="small">{(doc.hand_markers ?? []).map((m: any, i: number) => <li key={i}>{m.t}s {m.side} → {m.action}{m.until ? ` until ${m.until}s` : ""} <a href="#" onClick={(e) => { e.preventDefault(); onOp({ type: "performance.marker.remove", index: i }); }}>remove</a></li>)}</ul>
        <div className="row gap">
          <input value={saveName} onChange={(e) => setSaveName(e.target.value)} placeholder="name" style={{ width: 120 }} />
          <button onClick={() => { if (saveName) onOp({ type: "performance.save_as", name: saveName }); }} data-testid="perf-save-as">Save as</button>
          {info.library.map((n: string) => <button key={n} onClick={() => onOp({ type: "performance.load", name: n })}>load {n}</button>)}
        </div>
      </>}
    </div>
  );
}
