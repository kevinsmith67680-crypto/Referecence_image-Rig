import { useCallback, useEffect, useRef, useState } from "react";
import { api, Payload } from "./api";
import { focusRect, Pt, View } from "./geometry";
import CanvasView, { CompareSettings, MaskSettings, Mode, Overlays, Tool } from "./components/CanvasView";
import LayersPanel from "./components/LayersPanel";
import { ComparePanel, Cycle, ExportPanel, FacePanel, JointsPanel, LivePanel, MaskPanel, Motion, Perf, PosePanel, PropertiesPanel, VectorsPanel } from "./components/Panels";
import { IKPanel, SwitchesPanel } from "./components/RigPanels";
import { HandLibraryPanel } from "./components/HandPanels";
import { WeightBrush, WeightsPanel } from "./components/WeightsPanel";
import { CorrectivesPanel } from "./components/CorrectivesPanel";

const TABS = ["Properties", "Mask", "Joints", "Vectors", "Weights", "Correctives", "Pose", "IK", "Switches", "Hands", "Face", "Live", "Compare", "Export"] as const;
const POSE_TABS = ["Pose", "Correctives", "IK", "Switches", "Hands"];
type Tab = (typeof TABS)[number];
const TOOLS: { t: Tool; key: string; label: string }[] = [
  { t: "select", key: "v", label: "Select" }, { t: "pan", key: "h", label: "Pan" }, { t: "brush", key: "b", label: "Brush" },
  { t: "eraser", key: "e", label: "Eraser" }, { t: "polygon", key: "p", label: "Polygon" }, { t: "joint", key: "j", label: "Joint" },
  { t: "vector", key: "n", label: "Vector" }];

export default function App() {
  const [payload, setPayload] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("Properties");
  const [mode, setMode] = useState<Mode>("artwork");
  const [tool, setTool] = useState<Tool>("select");
  const [view, setView] = useState<View>({ scale: 0.5, x: 0, y: 0 });
  const [fit, setFit] = useState(0);
  const [selLayer, setSelLayer] = useState<string | null>(null);
  const [selBone, setSelBone] = useState<string | null>(null);
  const [selVec, setSelVec] = useState<string | null>(null);
  const [cursor, setCursor] = useState<Pt>({ x: 0, y: 0 });
  const [overlays, setOverlays] = useState<Overlays>({ checker: true, joints: true, bones: true, masks: true, baseMask: false,
    vectors: true, anchors: false, tears: true, boundaries: false, bounds: true });
  const [mask, setMask] = useState<MaskSettings>({ size: 14, hardness: 0.8, transferTo: "" });
  const [cmp, setCmp] = useState<CompareSettings>({ kind: "split", split: 0.5, opacity: 0.5 });
  const [preset, setPreset] = useState("relaxed_standing");
  const [overrides, setOverrides] = useState<Record<string, number>>({});
  const [poseRes, setPoseRes] = useState<any>(null);
  const [poseBusy, setPoseBusy] = useState(false);
  const [motion, setMotion] = useState<Motion>({ phase: 0.25, speed: 1, exaggeration: 1, allowPending: false,
    diagnostics: [], before: false, showBefore: false, liveTilt: 0, fluid: true });
  const [playing, setPlaying] = useState(false);
  const [skel, setSkel] = useState<any>(null);
  const [perf, setPerf] = useState<Perf>({ fps: 0, latencyMs: 0, solveMs: 0, frames: 0 });
  const [cycle, setCycle] = useState<Cycle | null>(null);
  const [anim, setAnim] = useState(false);
  const [flipIdx, setFlipIdx] = useState(0);
  const flipImgs = useRef<HTMLImageElement[]>([]);
  const [face, setFace] = useState<any>(null);
  const [live, setLive] = useState<any>(null);
  const [metrics, setMetrics] = useState<any>(null);
  const [gates, setGates] = useState<any>(null);
  const [exportRes, setExportRes] = useState<any>(null);
  const poseSeq = useRef(0);
  const [dragging, setDragging] = useState(false);
  const [handPreview, setHandPreview] = useState<{ side: string; cid: string } | null>(null);
  const [wbrush, setWbrush] = useState<WeightBrush>({ mode: "paint", bone: "upper_arm_r", radius: 24, strength: 0.5, hardness: 0.5, part: "", view: "parts" });
  const [wexplain, setWexplain] = useState<any>(null);
  const drag = useRef<{ busy: boolean; pending: any; t: number; lat: number[] }>({ busy: false, pending: null, t: 0, lat: [] });

  const rev = payload?.project.revision ?? "0";
  useEffect(() => { api.project().then(setPayload).catch((e) => setError(String(e))); }, []);
  const onOp = useCallback((op: Record<string, unknown>) => {
    api.op(op).then((p) => { setPayload(p); setError(null); }).catch((e) => setError(String(e.message ?? e)));
  }, []);
  const undo = () => api.undo().then(setPayload);
  const redo = () => api.redo().then(setPayload);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); return; }
      const t = TOOLS.find((x) => x.key === e.key.toLowerCase());
      if (t && !e.metaKey && !e.ctrlKey) setTool(t.t);
      if (e.key === "f") setFit((n) => n + 1);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  // tab-driven data and view modes
  useEffect(() => {
    if (POSE_TABS.includes(tab)) { setMode("pose"); if (tab !== "Pose" && preset === "rest") setPreset("relaxed_standing"); }
    else if (tab === "Compare") setMode("compare");
    else setMode("artwork");
    if (tab === "Face" || tab === "Live" || tab === "Compare" || tab === "Export") {
      api.face().then(setFace); api.live().then(setLive); api.metrics().then(setMetrics);
    }
    if (tab === "Mask" && !["brush", "eraser", "polygon"].includes(tool)) setTool("brush");
    if (tab === "Joints") setTool("joint");
    if (tab === "Vectors") setTool("vector");
    if (tab === "Weights") setTool("weights");
  }, [tab, rev]);
  const poseBody = (extra: Record<string, unknown> = {}) => ({ preset, overrides, boundaries: overlays.boundaries,
    phase: motion.phase, speed: motion.speed, exaggeration: motion.exaggeration, allow_pending: motion.allowPending,
    diagnostics: motion.diagnostics, before: motion.before, live_head: motion.liveTilt ? { tilt: motion.liveTilt } : null,
    fluid: motion.fluid, hand_preview: tab === "Hands" ? handPreview : null, ...extra });
  // animated cycle: full-artwork frames rendered by a background job, then played back frame by frame
  const renderCycle = (frames: number) => {
    setAnim(false);
    api.cycleStart(poseBody({ frames })).then((r) => setCycle({ id: r.id, total: r.frames, progress: 0, done: false }))
      .catch((e) => setError(String(e.message ?? e)));
  };
  useEffect(() => {
    if (!cycle || cycle.done) return;
    const t = setInterval(() => api.cycleStatus(cycle.id).then((s) => {
      if (s.error) { setError(s.error); setCycle({ ...cycle, done: true, error: s.error }); return; }
      if (!s.done) { setCycle({ ...cycle, progress: s.progress }); return; }
      const imgs = Array.from({ length: s.total }, (_, i) => { const im = new window.Image(); im.src = api.cycleFrameUrl(cycle.id, i); return im; });
      flipImgs.current = imgs;
      Promise.all(imgs.map((im) => im.decode().catch(() => null))).then(() => {
        setCycle({ ...cycle, progress: s.total, done: true, box: s.box, pad: s.pad, durationMs: s.duration_ms });
        setFlipIdx(0); setAnim(true);
      });
    }), 700);
    return () => clearInterval(t);
  }, [cycle]);
  useEffect(() => {
    if (!anim || !cycle?.durationMs) return;
    const t = setInterval(() => setFlipIdx((i) => (i + 1) % flipImgs.current.length), cycle.durationMs);
    return () => clearInterval(t);
  }, [anim, cycle?.durationMs]);
  useEffect(() => { setAnim(false); }, [preset, rev]);
  useEffect(() => {
    if (mode !== "pose" || !payload || playing) return;
    const seq = ++poseSeq.current;
    setPoseBusy(true);
    const t0 = performance.now();
    const t = setTimeout(() => api.pose(poseBody()).then((r) => {
      if (seq === poseSeq.current) { setPoseRes({ ...r, roundtrip_ms: Math.round(performance.now() - t0 - 350) }); setPoseBusy(false); setSkel(null); setDragging(false); }
    }).catch((e) => { setError(String(e.message ?? e)); setPoseBusy(false); }), 350);
    return () => clearTimeout(t);
  }, [mode, preset, overrides, rev, overlays.boundaries, motion, playing, handPreview, tab]);
  // interactive scrubbing: skeleton-only solves (milliseconds) as fast as the server answers; artwork after release
  useEffect(() => {
    if (!playing || mode !== "pose") return;
    let alive = true, ph = motion.phase, last = performance.now(), frames = 0, t1 = last, lat = 0, solve = 0;
    const step = async () => {
      while (alive) {
        const now = performance.now();
        ph = (ph + (now - last) / 1000 * 1.25 * motion.speed) % 1;  // ~1.25 cycles per second at speed 1
        last = now;
        const r = await api.pose(poseBody({ phase: ph, skeleton_only: true, diagnostics: [], before: false }));
        const done = performance.now();
        lat = done - now; solve = r.timing_ms?.solve ?? 0; frames++;
        if (!alive) break;
        setSkel(r);
        if (done - t1 > 500) { setPerf({ fps: Math.round(frames * 1000 / (done - t1)), latencyMs: Math.round(lat), solveMs: solve, frames }); frames = 0; t1 = done; }
        await new Promise((res) => requestAnimationFrame(() => res(null)));
      }
      setMotion((m) => ({ ...m, phase: Math.round(ph * 1000) / 1000 }));
    };
    step();
    return () => { alive = false; };
  }, [playing]);
  useEffect(() => { const t = setTimeout(() => api.gates().then(setGates).catch(() => {}), 1500); return () => clearTimeout(t); }, [rev]);

  // IK handle drags: skeleton-only solves with the draft IK state (one request in flight, latest position wins);
  // the operation is committed on release and the full artwork follows
  const ikDraft = (limb: string, kind: "target" | "pole", pt: Pt) => {
    const base = payload!.state.ik;
    const c = base[limb] ?? {};
    const L = (skel ?? poseRes)?.ik?.limbs?.[limb];
    const P = [+pt.x.toFixed(2), +pt.y.toFixed(2)];
    const fields: Record<string, unknown> = kind === "target" ? { enabled: true, mode: "ik", target: P } : { pole: P };
    if (kind === "target" && !(c.enabled && c.mode === "ik") && L && limb.startsWith("arm")) {
      const u = [L.effector[0] - L.root[0], L.effector[1] - L.root[1]], m = [L.mid[0] - L.root[0], L.mid[1] - L.root[1]];
      fields.bend = u[0] * m[1] - u[1] * m[0] >= 0 ? 1 : -1;  // hold the current elbow side (no inversion)
    }
    return { fields, ik: { ...base, [limb]: { ...c, ...fields } } };
  };
  const onIkDrag = (limb: string, kind: "target" | "pole", pt: Pt) => {
    const D = drag.current;
    D.pending = ikDraft(limb, kind, pt).ik;
    D.t = performance.now();
    if (D.busy) return;
    const pump = () => {
      const ik = D.pending;
      if (!ik) { D.busy = false; return; }
      D.pending = null; D.busy = true;
      const t0 = D.t;
      api.pose(poseBody({ ik, skeleton_only: true, diagnostics: [], before: false })).then((r) => {
        const lat = performance.now() - t0;
        D.lat.push(lat); (window as any).__autorigIkLatency = D.lat;
        setSkel(r); setDragging(true);
        setPerf((p) => ({ ...p, latencyMs: Math.round(lat), solveMs: r.timing_ms?.solve ?? 0, frames: p.frames + 1 }));
        pump();
      }).catch(() => { D.busy = false; });
    };
    pump();
  };
  const onWeightStroke = (pts: Pt[]) => {
    if (!selLayer || !pts.length) return;
    if (wbrush.mode === "explain") {
      const q = pts[pts.length - 1];
      api.garmentExplain(selLayer, q.x, q.y).then(setWexplain).catch((e) => setError(String(e.message ?? e)));
      return;
    }
    const base: Record<string, unknown> = { layer: selLayer, radius: wbrush.radius, strength: wbrush.strength, hardness: wbrush.hardness };
    if (wbrush.part) base.part = wbrush.part;
    const type = wbrush.mode === "unlock" ? "weights.lock" : `weights.${wbrush.mode}`;
    // one operation per dab along the stroke, spaced by half the radius
    const dabs: Pt[] = [];
    for (const q of pts) if (!dabs.length || Math.hypot(q.x - dabs[dabs.length - 1].x, q.y - dabs[dabs.length - 1].y) >= wbrush.radius / 2) dabs.push(q);
    (async () => {
      for (const q of dabs) {
        const op: Record<string, unknown> = { ...base, type, center: [+q.x.toFixed(1), +q.y.toFixed(1)] };
        if (wbrush.mode === "paint" || wbrush.mode === "erase") op.bone = wbrush.bone;
        if (wbrush.mode === "lock" || wbrush.mode === "unlock") op.locked = wbrush.mode === "lock";
        await api.op(op).then((p) => { setPayload(p); setError(null); }).catch((e) => setError(String(e.message ?? e)));
      }
    })();
  };
  const onIkDragEnd = (limb: string, kind: "target" | "pole", pt: Pt) => {
    drag.current.pending = null;
    onOp({ type: "ik.set", limb, fields: ikDraft(limb, kind, pt).fields });
  };

  if (!payload) return <div className="loading">{error ? `Cannot reach the editor backend: ${error}` : "Loading character project…"}</div>;
  const gl = gates?.overall ?? "…";
  const focusJoint = () => {
    if (!selBone) return;
    const pos = mode === "pose" && poseRes ? poseRes.joints_posed[selBone] : payload.state.bones[selBone].head;
    const box = document.querySelector("[data-testid=canvas]") as HTMLElement;
    setView(focusRect([pos[0] - 90, pos[1] - 90, pos[0] + 90, pos[1] + 90], box.clientWidth, box.clientHeight));
  };

  return (
    <div className="app">
      <header className="toolbar">
        <span className="brand">AutoRig Studio</span>
        <div className="tools">{TOOLS.map((x) => <button key={x.t} className={tool === x.t ? "on" : ""} title={`${x.label} (${x.key.toUpperCase()})`}
          onClick={() => setTool(x.t)} data-testid={`tb-${x.t}`}>{x.label}</button>)}</div>
        <div className="sep" />
        <button onClick={undo} disabled={!payload.project.can_undo} data-testid="undo" title="Undo (Cmd/Ctrl+Z)">↶ Undo</button>
        <button onClick={redo} disabled={!payload.project.can_redo} data-testid="redo" title="Redo (Shift+Cmd/Ctrl+Z)">↷ Redo</button>
        <button onClick={() => { const l = prompt("Version label", "checkpoint"); if (l !== null) api.saveVersion(l).then(() => api.project().then(setPayload)); }} data-testid="save-version">Save version</button>
        <select value="" onChange={(e) => e.target.value && api.restoreVersion(+e.target.value).then(setPayload)}>
          <option value="">Versions ({payload.project.versions.length})</option>
          {payload.project.versions.map((v: any) => <option key={v.n} value={v.n}>v{v.n} {v.label}</option>)}</select>
        <div className="sep" />
        <button onClick={() => setFit((n) => n + 1)} title="Fit to screen (F)">Fit</button>
        <button onClick={() => setView({ ...view, scale: view.scale * 1.25 })}>＋</button>
        <button onClick={() => setView({ ...view, scale: view.scale / 1.25 })}>－</button>
        <button onClick={focusJoint} disabled={!selBone} title="Zoom to the selected joint">Zoom joint</button>
        <div className="sep" />
        {(["checker", "joints", "bones", "masks", "vectors", "bounds"] as const).map((o) => (
          <label key={o} className="check small"><input type="checkbox" checked={overlays[o]} onChange={(e) => setOverlays({ ...overlays, [o]: e.target.checked })} />{o}</label>))}
        <div className="grow" />
        <span className={`ready r-${gl.toLowerCase()}`} data-testid="readiness" title="Export readiness (quality gates)">Export: {gl}</span>
      </header>
      <main>
        <aside className="left">
          <LayersPanel payload={payload} selected={selLayer} onSelect={(id) => { setSelLayer(id); const b = payload.state.layers.find((l) => l.id === id)?.bone; if (b) setSelBone(b); }} onOp={onOp} />
          <div className="panel ref"><div className="panel-title">Reference</div><img src={api.sourceUrl()} alt="original reference" data-testid="reference" /></div>
        </aside>
        <section className="center">
          <CanvasView payload={payload} rev={rev} mode={mode} tool={tool} view={view} setView={setView} fitSignal={fit}
            selectedLayer={selLayer} onSelectLayer={setSelLayer} selectedBone={selBone} onSelectBone={setSelBone}
            selectedVector={selVec} onSelectVector={setSelVec} overlays={overlays} mask={mask} compare={cmp}
            pose={poseRes} face={face} onOp={onOp} onCursor={setCursor} skel={playing || dragging ? skel : null} showBefore={motion.showBefore && motion.before}
            ikHandles={tab === "IK"} onIkDrag={onIkDrag} onIkDragEnd={onIkDragEnd}
            garmentOverlay={tab === "Weights" && wbrush.view !== "none" ? api.garmentOverlayUrl(wbrush.view, wbrush.view === "parts" ? null : selLayer, wbrush.bone, rev) : null}
            weightRadius={wbrush.radius} onWeightStroke={onWeightStroke}
            flip={anim && cycle?.box && flipImgs.current[flipIdx] ? { img: flipImgs.current[flipIdx], x: cycle.box[0] - (cycle.pad ?? 0), y: cycle.box[1] - (cycle.pad ?? 0) } : null} />
          <div className="modebar">
            {(["artwork", "pose", "compare"] as Mode[]).map((m) => <button key={m} className={mode === m ? "on" : ""} onClick={() => setTab(m === "pose" ? "Pose" : m === "compare" ? "Compare" : "Properties")}>{m}</button>)}
          </div>
        </section>
        <aside className="right">
          <nav className="tabs">{TABS.map((t) => <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)} data-testid={`tab-${t}`}>{t}</button>)}</nav>
          <div className="tab-body">
            {tab === "Properties" && <PropertiesPanel payload={payload} id={selLayer} onOp={onOp} />}
            {tab === "Mask" && <MaskPanel payload={payload} id={selLayer} tool={tool} setTool={setTool} mask={mask} setMask={setMask} overlays={overlays} setOverlays={setOverlays} onOp={onOp} />}
            {tab === "Joints" && <JointsPanel payload={payload} bone={selBone} onSelect={setSelBone} onOp={onOp} />}
            {tab === "Vectors" && <VectorsPanel payload={payload} sel={selVec} onSelect={setSelVec} bone={selBone} onOp={onOp} />}
            {tab === "Weights" && <WeightsPanel payload={payload} layer={selLayer} rev={rev} brush={wbrush} setBrush={setWbrush}
              explain={wexplain} onOp={onOp} onError={setError} preset={preset} />}
            {tab === "Correctives" && <CorrectivesPanel payload={payload} rev={rev} preset={preset} phase={motion.phase} onOp={onOp} onError={setError}
              canvasBusy={poseBusy} canvasResult={poseRes} />}
            {tab === "Pose" && <PosePanel payload={payload} preset={preset} setPreset={setPreset} overrides={overrides} setOverrides={setOverrides}
              result={poseRes} busy={poseBusy} bone={selBone} onSelectBone={setSelBone} overlays={overlays} setOverlays={setOverlays} onOp={onOp}
              motion={motion} setMotion={setMotion} playing={playing} setPlaying={setPlaying} perf={perf}
              cycle={cycle} anim={anim} setAnim={setAnim} onRenderCycle={renderCycle} flipIdx={flipIdx}
              onCloseup={(n) => { const pos = (skel ?? poseRes)?.joints_posed?.[n]; const box = document.querySelector("[data-testid=canvas]") as HTMLElement;
                if (pos && box) setView(focusRect([pos[0] - 110, pos[1] - 110, pos[0] + 110, pos[1] + 110], box.clientWidth, box.clientHeight)); }} />}
            {tab === "IK" && <IKPanel payload={payload} result={dragging && skel ? skel : poseRes} busy={poseBusy || dragging} onOp={onOp} onError={setError}
              ctx={{ preset, phase: motion.phase, speed: motion.speed, exaggeration: motion.exaggeration, allowPending: motion.allowPending, fluid: motion.fluid }} />}
            {tab === "Switches" && <SwitchesPanel payload={payload} result={poseRes} rev={rev} phase={motion.phase} onOp={onOp} onError={setError} />}
            {tab === "Hands" && <HandLibraryPanel payload={payload} rev={rev} onOp={onOp} onError={setError}
              onPreviewPose={(side, cid) => setHandPreview({ side, cid })}
              previewStatus={!handPreview ? "" : poseBusy ? "rendering…" : poseRes?.hands?.[handPreview.side]?.preview_override ? "shown on the canvas (preview)" : ""} />}
            {tab === "Face" && <FacePanel face={face} overlays={overlays} setOverlays={setOverlays} onOp={onOp} />}
            {tab === "Live" && <LivePanel live={live} />}
            {tab === "Compare" && <ComparePanel cmp={cmp} setCmp={setCmp} metrics={metrics} payload={payload} onOp={onOp} />}
            {tab === "Export" && <ExportPanel gates={gates} payload={payload} onOp={onOp} onRefresh={() => api.gates().then(setGates)}
              onExport={() => api.exportPkg().then((r) => { setExportRes(r); api.gates().then(setGates); })} exportResult={exportRes} />}
          </div>
        </aside>
      </main>
      <footer className="status">
        <span data-testid="cursor">x {cursor.x.toFixed(1)} · y {cursor.y.toFixed(1)} px</span>
        <span>zoom {(view.scale * 100).toFixed(0)}%</span>
        <span>mode {mode} · tool {tool}</span>
        <span data-testid="revision">rev {rev} · ops {payload.project.cursor}/{payload.project.ops}</span>
        <span>layer {selLayer ?? "-"} · bone {selBone ?? "-"}</span>
        {error && <span className="err" data-testid="error">⚠ {error}</span>}
      </footer>
    </div>
  );
}
