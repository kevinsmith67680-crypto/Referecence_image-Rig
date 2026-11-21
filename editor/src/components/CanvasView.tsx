import { useEffect, useMemo, useRef, useState } from "react";
import { Circle, Group, Image as KImage, Layer, Line, Path, Rect, Stage } from "react-konva";
import type Konva from "konva";
import { api, Payload } from "../api";
import { effectiveBrushRadius, fitView, pathD, pixelKey, Pt, View, zoomAt } from "../geometry";

export type Tool = "select" | "pan" | "brush" | "eraser" | "polygon" | "joint" | "vector";
export type Mode = "artwork" | "pose" | "compare";
export interface Overlays { checker: boolean; joints: boolean; bones: boolean; masks: boolean; baseMask: boolean;
  vectors: boolean; anchors: boolean; tears: boolean; boundaries: boolean; bounds: boolean }
export interface MaskSettings { size: number; hardness: number; transferTo: string }
export interface CompareSettings { kind: "split" | "overlay" | "diff" | "provenance" | "reference"; split: number; opacity: number }

const registry = new Map<string, HTMLImageElement>();

function useImg(url: string | null, key?: string): HTMLImageElement | undefined {
  // Keeps showing the previous image until the new one has loaded (no flicker), ignores stale loads.
  const [img, setImg] = useState<HTMLImageElement>();
  useEffect(() => {
    if (!url) { setImg(undefined); return; }
    let live = true;
    const im = new window.Image();
    im.onload = () => { if (!live) return; setImg(im); if (key) registry.set(key, im); };
    im.src = url;
    return () => { live = false; };
  }, [url, key]);
  return img;
}

function useTinted(url: string | null, color: string): HTMLCanvasElement | undefined {
  const img = useImg(url);
  return useMemo(() => {
    if (!img) return undefined;
    const c = document.createElement("canvas");
    c.width = img.width; c.height = img.height;
    const ctx = c.getContext("2d")!;
    ctx.drawImage(img, 0, 0);
    ctx.globalCompositeOperation = "source-in";
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, c.width, c.height);
    return c;
  }, [img, color]);
}

let checker: HTMLCanvasElement | undefined;
function checkerPattern(): HTMLCanvasElement {
  if (checker) return checker;
  const c = document.createElement("canvas");
  c.width = c.height = 32;
  const ctx = c.getContext("2d")!;
  ctx.fillStyle = "#cfd3da"; ctx.fillRect(0, 0, 32, 32);
  ctx.fillStyle = "#e9ebef"; ctx.fillRect(0, 0, 16, 16); ctx.fillRect(16, 16, 16, 16);
  checker = c;
  return c;
}

const probe = typeof document !== "undefined" ? document.createElement("canvas") : null;
function alphaAt(img: HTMLImageElement | undefined, x: number, y: number): number {
  if (!img || !probe || x < 0 || y < 0 || x >= img.width || y >= img.height) return 0;
  probe.width = probe.height = 1;
  const ctx = probe.getContext("2d", { willReadFrequently: true })!;
  ctx.clearRect(0, 0, 1, 1);
  ctx.drawImage(img, -Math.floor(x), -Math.floor(y));
  return ctx.getImageData(0, 0, 1, 1).data[3];
}

function alphaBounds(img: HTMLImageElement | undefined): [number, number, number, number] | null {
  if (!img) return null;
  const c = document.createElement("canvas");
  c.width = img.width; c.height = img.height;
  const ctx = c.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(img, 0, 0);
  const d = ctx.getImageData(0, 0, c.width, c.height).data;
  let x0 = c.width, y0 = c.height, x1 = -1, y1 = -1;
  for (let y = 0; y < c.height; y++) for (let x = 0; x < c.width; x++) {
    if (d[(y * c.width + x) * 4 + 3] > 0) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
  }
  return x1 < 0 ? null : [x0, y0, x1 + 1, y1 + 1];
}

function LayerImg({ id, rev, opacity }: { id: string; rev: string; opacity: number }) {
  const img = useImg(api.layerUrl(id, rev), id);
  return img ? <KImage image={img} opacity={opacity} listening={false} /> : null;
}

const sideColor = (side: string | null) => (side === "r" ? "#ff9f43" : side === "l" ? "#48dbfb" : "#feca57");

export interface CanvasProps {
  payload: Payload; rev: string; mode: Mode; tool: Tool; view: View; setView: (v: View) => void;
  selectedLayer: string | null; onSelectLayer: (id: string | null) => void;
  selectedBone: string | null; onSelectBone: (b: string | null) => void;
  selectedVector: string | null; onSelectVector: (v: string | null) => void;
  overlays: Overlays; mask: MaskSettings; compare: CompareSettings; pose: any | null; face: any | null;
  onOp: (op: Record<string, unknown>) => void; onCursor: (p: Pt) => void; fitSignal: number;
  skel?: any | null; showBefore?: boolean;
}

function DiagImg({ src, pad }: { src: string; pad: number }) {
  const img = useImg(src);
  return img ? <KImage image={img} x={-pad} y={-pad} listening={false} /> : null;
}

export default function CanvasView(p: CanvasProps) {
  const { payload, rev, view } = p;
  const st = payload.state;
  const W = st.canvas.width, H = st.canvas.height;
  const pix = useMemo(() => pixelKey(st.mask_ops, st.approvals), [st.mask_ops, st.approvals]);
  const box = useRef<HTMLDivElement>(null);
  const stage = useRef<Konva.Stage>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [stroke, setStroke] = useState<Pt[] | null>(null);
  const [poly, setPoly] = useState<Pt[]>([]);
  const [dragBones, setDragBones] = useState<Record<string, { head?: number[]; tail?: number[] }>>({});
  const [dragVec, setDragVec] = useState<any[] | null>(null);
  const [bounds, setBounds] = useState<[number, number, number, number] | null>(null);

  useEffect(() => {
    const el = box.current!;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);
  useEffect(() => { if (size.w > 50) p.setView(fitView(W, H, size.w, size.h)); }, [p.fitSignal, size.w, size.h]);
  useEffect(() => { (window as any).__autorigView = view; }, [view]); // test hook: image <-> screen mapping
  useEffect(() => { setDragBones({}); setDragVec(null); }, [rev]);
  useEffect(() => {
    const t = setTimeout(() => setBounds(p.selectedLayer ? alphaBounds(registry.get(p.selectedLayer)) : null), 250);
    return () => clearTimeout(t);
  }, [p.selectedLayer, rev]);

  const order = st.layers.filter((l) => l.visible);
  const maskTint = useTinted(p.overlays.masks && p.selectedLayer && p.mode === "artwork" ?
    api.maskUrl(p.selectedLayer, pix) : null, "rgba(0,200,255,1)");
  const baseTint = useTinted(p.overlays.baseMask && p.selectedLayer && p.mode === "artwork" ?
    api.maskUrl(p.selectedLayer, pix, true) : null, "rgba(255,0,200,1)");
  const poseImg = useImg(p.mode === "pose" && p.pose ? (p.showBefore && p.pose.before ? p.pose.before : p.pose.image) : null);
  const sk = p.skel ?? p.pose;  // skeleton-only draft while scrubbing, else the rendered pose
  const boundImg = useImg(p.mode === "pose" && p.pose?.boundaries && p.overlays.boundaries ? p.pose.boundaries : null);
  const source = useImg(api.sourceUrl());
  const neutral = useImg(p.mode === "compare" ? api.compareUrl("neutral", rev) : null);
  const diff = useImg(p.mode === "compare" && p.compare.kind === "diff" ? api.compareUrl("diff", rev) : null);
  const prov = useImg(p.mode === "compare" && p.compare.kind === "provenance" ? api.compareUrl("provenance", rev) : null);

  const ip = (): Pt => stage.current!.getRelativePointerPosition()!;
  const k = 1 / view.scale; // constant on-screen size for handles

  function onWheel(e: Konva.KonvaEventObject<WheelEvent>) {
    e.evt.preventDefault();
    const pt = stage.current!.getPointerPosition()!;
    p.setView(zoomAt(view, pt, e.evt.deltaY < 0 ? 1.15 : 1 / 1.15));
  }
  function onDown(e: Konva.KonvaEventObject<MouseEvent>) {
    if (e.evt.button !== 0) return;
    const pt = ip();
    if (p.mode !== "artwork") return;
    if ((p.tool === "brush" || p.tool === "eraser") && p.selectedLayer) setStroke([pt]);
  }
  function onMove() {
    const pt = ip();
    p.onCursor(pt);
    if (stroke) setStroke([...stroke, pt]);
  }
  function onUp() {
    if (stroke && p.selectedLayer) {
      const pts = stroke.filter((_, i) => i % 2 === 0 || i === stroke.length - 1).map((q) => [+q.x.toFixed(1), +q.y.toFixed(1)]);
      p.onOp({ type: "mask.edit", layer: p.selectedLayer, mode: p.tool === "brush" ? "add" : "subtract",
               transfer_to: p.tool === "eraser" && p.mask.transferTo ? p.mask.transferTo : null,
               shape: { type: "stroke", points: pts, radius: p.mask.size, hardness: p.mask.hardness } });
    }
    setStroke(null);
  }
  function onClick(e: Konva.KonvaEventObject<MouseEvent>) {
    if (p.mode !== "artwork") return;
    const pt = ip();
    if (p.tool === "select") {
      for (const l of [...order].reverse()) {
        if (alphaAt(registry.get(l.id), pt.x, pt.y) > 0) { p.onSelectLayer(l.id); return; }
      }
      p.onSelectLayer(null);
    } else if (p.tool === "polygon" && p.selectedLayer) {
      setPoly((q) => [...q, pt]);
    }
  }
  function closePolygon(mode: "add" | "subtract") {
    if (poly.length >= 3 && p.selectedLayer) {
      p.onOp({ type: "mask.edit", layer: p.selectedLayer, mode, transfer_to: mode === "subtract" && p.mask.transferTo ? p.mask.transferTo : null,
               shape: { type: "polygon", points: poly.map((q) => [+q.x.toFixed(1), +q.y.toFixed(1)]) } });
    }
    setPoly([]);
  }
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (p.tool !== "polygon") return;
      if (e.key === "Enter") closePolygon(e.shiftKey ? "subtract" : "add");
      if (e.key === "Escape") setPoly([]);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });
  (window as any).__autorigClosePolygon = closePolygon;

  const bones = Object.entries(st.bones);
  const hasChild = new Set(bones.map(([, b]) => b.parent).filter(Boolean));
  const bh = (n: string) => dragBones[n]?.head ?? st.bones[n].head;
  const bt = (n: string) => dragBones[n]?.tail ?? st.bones[n].tail;
  const pad = p.pose?.pad ?? 0;
  const vecs = Object.values(st.vectors).filter((v) => v.visible);
  const selVec = p.selectedVector ? st.vectors[p.selectedVector] : null;
  const selNodes = dragVec ?? selVec?.nodes ?? [];

  return (
    <div ref={box} className="canvas-box" data-testid="canvas">
      <Stage ref={stage} width={size.w} height={size.h} scaleX={view.scale} scaleY={view.scale} x={view.x} y={view.y}
        draggable={p.tool === "pan"} onDragEnd={(e) => { if (e.target === stage.current) p.setView({ ...view, x: e.target.x(), y: e.target.y() }); }}
        onWheel={onWheel} onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp} onClick={onClick}
        onDblClick={() => p.tool === "polygon" && poly.length >= 3 && closePolygon("add")}>
        <Layer listening={false}>
          {p.overlays.checker && <Rect x={-pad} y={-pad} width={W + 2 * pad} height={H + 2 * pad} fillPatternImage={checkerPattern() as any} />}
          {!p.overlays.checker && <Rect x={-pad} y={-pad} width={W + 2 * pad} height={H + 2 * pad} fill="#f4f5f7" />}
          {p.mode === "artwork" && order.map((l) => <LayerImg key={l.id} id={l.id} rev={`${pix}-${l.edge_feather ?? 0}`} opacity={l.opacity} />)}
          {p.mode === "pose" && poseImg && <KImage image={poseImg} x={-pad} y={-pad} />}
          {p.mode === "pose" && boundImg && <KImage image={boundImg} x={-pad} y={-pad} opacity={0.9} />}
          {p.mode === "pose" && p.pose?.diagnostics && !p.showBefore && Object.entries(p.pose.diagnostics).map(([k, src]: any) => (
            <DiagImg key={k} src={src} pad={pad} />))}
          {p.mode === "compare" && source && <KImage image={source} opacity={p.compare.kind === "diff" ? 0.3 : 1} />}
          {p.mode === "compare" && neutral && p.compare.kind === "split" && (
            <Group clipFunc={(ctx) => ctx.rect(0, 0, W * p.compare.split, H)}><KImage image={neutral} /></Group>)}
          {p.mode === "compare" && neutral && p.compare.kind === "overlay" && <KImage image={neutral} opacity={p.compare.opacity} />}
          {p.mode === "compare" && p.compare.kind === "diff" && diff && <KImage image={diff} />}
          {p.mode === "compare" && p.compare.kind === "provenance" && neutral && <KImage image={neutral} opacity={0.5} />}
          {p.mode === "compare" && p.compare.kind === "provenance" && prov && <KImage image={prov} />}
          {p.mode === "compare" && p.compare.kind === "split" && <Line points={[W * p.compare.split, 0, W * p.compare.split, H]} stroke="#ff3366" strokeWidth={2 * k} />}
        </Layer>
        <Layer>
          {p.mode === "artwork" && maskTint && <KImage image={maskTint} opacity={0.38} listening={false} />}
          {p.mode === "artwork" && baseTint && <KImage image={baseTint} opacity={0.3} listening={false} />}
          {p.overlays.bounds && bounds && p.mode === "artwork" && (
            <Rect x={bounds[0]} y={bounds[1]} width={bounds[2] - bounds[0]} height={bounds[3] - bounds[1]}
              stroke="#4dabf7" dash={[6 * k, 4 * k]} strokeWidth={1.5 * k} listening={false} />)}
          {p.mode === "artwork" && p.overlays.vectors && vecs.map((v) => (
            <Path key={v.id} data={pathD(v.id === p.selectedVector && dragVec ? dragVec : v.nodes, v.closed)}
              fill={v.closed ? v.fill : undefined} stroke={v.stroke} strokeWidth={v.stroke_width}
              opacity={v.id === p.selectedVector ? 1 : 0.85} hitStrokeWidth={12 * k}
              onClick={(e) => { if (p.tool === "vector") { e.cancelBubble = true; p.onSelectVector(v.id); } }} />))}
          {p.mode === "artwork" && p.tool === "vector" && selVec && selNodes.map((n: any, i: number) => (
            <Group key={i}>
              {n.hin && <Line points={[n.x, n.y, n.x + n.hin[0], n.y + n.hin[1]]} stroke="#ffd43b" strokeWidth={1 * k} />}
              {n.hout && <Line points={[n.x, n.y, n.x + n.hout[0], n.y + n.hout[1]]} stroke="#ffd43b" strokeWidth={1 * k} />}
              {(["hin", "hout"] as const).map((h) => n[h] && (n[h][0] || n[h][1]) ? (
                <Circle key={h} x={n.x + n[h][0]} y={n.y + n[h][1]} radius={4 * k} fill="#ffd43b" draggable
                  onDragMove={(e) => setDragVec(selNodes.map((m: any, j: number) => j === i ? { ...m, [h]: [e.target.x() - m.x, e.target.y() - m.y] } : m))}
                  onDragEnd={() => dragVec && p.onOp({ type: "vector.update", id: selVec.id, fields: { nodes: dragVec } })} />) : null)}
              <Rect x={n.x - 5 * k} y={n.y - 5 * k} width={10 * k} height={10 * k} fill="#fff" stroke="#e8590c" strokeWidth={2 * k}
                draggable data-testid={`vnode-${i}`}
                onDragMove={(e) => setDragVec(selNodes.map((m: any, j: number) => j === i ? { ...m, x: e.target.x() + 5 * k, y: e.target.y() + 5 * k } : m))}
                onDragEnd={() => dragVec && p.onOp({ type: "vector.update", id: selVec.id, fields: { nodes: dragVec } })} />
            </Group>))}
          {(p.mode === "artwork") && p.overlays.bones && bones.map(([n, b]) => (
            <Line key={n} points={[...bh(n), ...bt(n)]} stroke={sideColor(b.side)} strokeWidth={(n === p.selectedBone ? 4 : 2) * k}
              opacity={0.9} listening={false} />))}
          {p.mode === "pose" && sk && p.overlays.bones && !p.showBefore && Object.entries(sk.joints_posed).map(([n, h]: any) => (
            <Line key={n} points={[...h, ...sk.bone_tails_posed[n]]} stroke={sideColor(st.bones[n]?.side ?? null)} strokeWidth={(p.skel ? 4 : 2) * k} listening={false} />))}
          {p.mode === "pose" && sk && p.overlays.joints && !p.showBefore && Object.entries(sk.joints_posed).map(([n, h]: any) => (
            <Circle key={n} x={h[0]} y={h[1]} radius={(n === p.selectedBone ? 7 : 5) * k} fill={n === p.selectedBone ? "#ff6b6b" : "#1c7ed6"} stroke="#fff" strokeWidth={1.5 * k}
              onClick={() => p.onSelectBone(n)} />))}
          {p.mode === "pose" && p.pose && p.overlays.tears && (p.pose.tear_points ?? []).map((t: number[], i: number) => (
            <Circle key={i} x={t[0] - pad} y={t[1] - pad} radius={3 * k} fill="#ff0000" listening={false} />))}
          {p.mode === "artwork" && p.overlays.joints && bones.map(([n, b]) => (
            <Circle key={n} x={bh(n)[0]} y={bh(n)[1]} radius={(n === p.selectedBone ? 8 : 6) * k}
              fill={n === p.selectedBone ? "#ff6b6b" : "#1c7ed6"} stroke="#fff" strokeWidth={1.5 * k}
              draggable={p.tool === "joint"} name={`joint-${n}`}
              onClick={() => p.onSelectBone(n)}
              onDragStart={() => p.onSelectBone(n)}
              onDragMove={(e) => setDragBones({ ...dragBones, [n]: { ...dragBones[n], head: [e.target.x(), e.target.y()] } })}
              onDragEnd={(e) => p.onOp({ type: "bone.set", name: n, fields: { head: [+e.target.x().toFixed(2), +e.target.y().toFixed(2)] } })} />))}
          {p.mode === "artwork" && p.overlays.joints && p.tool === "joint" && bones.filter(([n]) => !hasChild.has(n)).map(([n, b]) => (
            <Rect key={n + "t"} x={bt(n)[0] - 5 * k} y={bt(n)[1] - 5 * k} width={10 * k} height={10 * k} fill="#fff" stroke={sideColor(b.side)}
              strokeWidth={2 * k} draggable
              onDragMove={(e) => setDragBones({ ...dragBones, [n]: { ...dragBones[n], tail: [e.target.x() + 5 * k, e.target.y() + 5 * k] } })}
              onDragEnd={(e) => p.onOp({ type: "bone.set", name: n, fields: { tail: [+(e.target.x() + 5 * k).toFixed(2), +(e.target.y() + 5 * k).toFixed(2)] } })} />))}
          {p.overlays.anchors && p.face && Object.entries(p.face.anchors ?? {}).filter(([, v]) => Array.isArray(v)).map(([n, v]: any) => (
            <Group key={n} listening={false}>
              <Line points={[v[0] - 6 * k, v[1], v[0] + 6 * k, v[1]]} stroke="#e64980" strokeWidth={2 * k} />
              <Line points={[v[0], v[1] - 6 * k, v[0], v[1] + 6 * k]} stroke="#e64980" strokeWidth={2 * k} />
            </Group>))}
          {stroke && <Line points={stroke.flatMap((q) => [q.x, q.y])} stroke={p.tool === "brush" ? "#40c057" : "#fa5252"}
            strokeWidth={2 * effectiveBrushRadius(p.mask.size, p.mask.hardness)} lineCap="round" lineJoin="round" opacity={0.45} listening={false} />}
          {poly.length > 0 && <Line points={poly.flatMap((q) => [q.x, q.y])} closed={poly.length > 2} stroke="#fab005"
            strokeWidth={2 * k} dash={[6 * k, 4 * k]} fill="rgba(250,176,5,0.15)" listening={false} />}
        </Layer>
      </Stage>
    </div>
  );
}
