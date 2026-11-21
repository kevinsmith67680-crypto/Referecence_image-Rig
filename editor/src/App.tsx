import { useCallback, useEffect, useRef, useState } from "react";
import { api, Payload } from "./api";
import { focusRect, Pt, View } from "./geometry";
import CanvasView, { CompareSettings, MaskSettings, Mode, Overlays, Tool } from "./components/CanvasView";
import LayersPanel from "./components/LayersPanel";
import { ComparePanel, ExportPanel, FacePanel, JointsPanel, LivePanel, MaskPanel, PosePanel, PropertiesPanel, VectorsPanel } from "./components/Panels";

const TABS = ["Properties", "Mask", "Joints", "Vectors", "Pose", "Face", "Live", "Compare", "Export"] as const;
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
  const [face, setFace] = useState<any>(null);
  const [live, setLive] = useState<any>(null);
  const [metrics, setMetrics] = useState<any>(null);
  const [gates, setGates] = useState<any>(null);
  const [exportRes, setExportRes] = useState<any>(null);
  const poseSeq = useRef(0);

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
    if (tab === "Pose") setMode("pose");
    else if (tab === "Compare") setMode("compare");
    else setMode("artwork");
    if (tab === "Face" || tab === "Live" || tab === "Compare" || tab === "Export") {
      api.face().then(setFace); api.live().then(setLive); api.metrics().then(setMetrics);
    }
    if (tab === "Mask" && !["brush", "eraser", "polygon"].includes(tool)) setTool("brush");
    if (tab === "Joints") setTool("joint");
    if (tab === "Vectors") setTool("vector");
  }, [tab, rev]);
  useEffect(() => {
    if (mode !== "pose" || !payload) return;
    const seq = ++poseSeq.current;
    setPoseBusy(true);
    const t = setTimeout(() => api.pose(preset, overrides, overlays.boundaries).then((r) => {
      if (seq === poseSeq.current) { setPoseRes(r); setPoseBusy(false); }
    }).catch((e) => { setError(String(e.message ?? e)); setPoseBusy(false); }), 350);
    return () => clearTimeout(t);
  }, [mode, preset, overrides, rev, overlays.boundaries]);
  useEffect(() => { const t = setTimeout(() => api.gates().then(setGates).catch(() => {}), 1500); return () => clearTimeout(t); }, [rev]);

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
            pose={poseRes} face={face} onOp={onOp} onCursor={setCursor} />
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
            {tab === "Pose" && <PosePanel payload={payload} preset={preset} setPreset={setPreset} overrides={overrides} setOverrides={setOverrides}
              result={poseRes} busy={poseBusy} bone={selBone} onSelectBone={setSelBone} overlays={overlays} setOverlays={setOverlays} onOp={onOp} />}
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
