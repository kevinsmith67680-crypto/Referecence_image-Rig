import { useState } from "react";
import { Layer, Payload } from "../api";
import { buildTree, TreeGroup } from "../geometry";

const CAT_COLOR: Record<string, string> = { original: "#51cf66", extracted: "#94d82d", reconstructed: "#cc5de8",
  vector_corrected: "#fcc419", reference_derived: "#4dabf7", approximation: "#ff922b" };

interface Props { payload: Payload; selected: string | null; onSelect: (id: string) => void; onOp: (op: any) => void }

export default function LayersPanel({ payload, selected, onSelect, onOp }: Props) {
  const st = payload.state;
  const tree = buildTree(st.groups, st.layers);
  const byId: Record<string, Layer> = Object.fromEntries(st.layers.map((l) => [l.id, l]));
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const index = (id: string) => st.layers.findIndex((l) => l.id === id);

  function Row({ id, depth }: { id: string; depth: number }) {
    const l = byId[id];
    const warn = payload.mask_validation.layers[id]?.warnings?.length;
    return (
      <div className={`layer-row ${selected === id ? "sel" : ""} ${l.visible ? "" : "hidden"}`} style={{ paddingLeft: 8 + depth * 14 }}
           onClick={() => onSelect(id)} data-testid={`layer-${id}`}>
        <button className="icon" title={l.visible ? "Hide" : "Show"} data-testid={`vis-${id}`}
          onClick={(e) => { e.stopPropagation(); onOp({ type: "layer.set", id, fields: { visible: !l.visible } }); }}>{l.visible ? "◉" : "○"}</button>
        <button className="icon" title={l.locked ? "Unlock" : "Lock"}
          onClick={(e) => { e.stopPropagation(); onOp({ type: "layer.set", id, fields: { locked: !l.locked } }); }}>{l.locked ? "🔒" : "🔓"}</button>
        <span className="swatch" style={{ background: CAT_COLOR[l.user_category] ?? "#868e96" }} title={`${l.user_category} · ${l.provenance}`} />
        <span className="lname">{l.name}{l.kind === "switch" ? " ⇄" : ""}</span>
        {warn ? <span className="warn" title={payload.mask_validation.layers[id].warnings.join("; ")}>⚠</span> : null}
        <span className="z">{index(id)}</span>
        <button className="icon" title="Move up (toward front)" data-testid={`up-${id}`}
          onClick={(e) => { e.stopPropagation(); onOp({ type: "layer.reorder", id, to_index: index(id) + 1 }); }}>▲</button>
        <button className="icon" title="Move down (toward back)" data-testid={`down-${id}`}
          onClick={(e) => { e.stopPropagation(); onOp({ type: "layer.reorder", id, to_index: Math.max(0, index(id) - 1) }); }}>▼</button>
      </div>
    );
  }

  function GroupView({ g, depth }: { g: TreeGroup; depth: number }) {
    const isOpen = open[g.id] ?? true;
    return (
      <div>
        <div className="group-row" style={{ paddingLeft: 4 + depth * 14 }} onClick={() => setOpen({ ...open, [g.id]: !isOpen })}>
          <span>{isOpen ? "▾" : "▸"}</span> <b>{g.name}</b>
          {g.id !== "CHARACTER" && <button className="icon small" title="Ungroup"
            onClick={(e) => { e.stopPropagation(); onOp({ type: "group.ungroup", id: g.id }); }}>⊟</button>}
        </div>
        {isOpen && <>
          {g.children.map((c) => <GroupView key={c.id} g={c} depth={depth + 1} />)}
          {[...g.layers].reverse().map((id) => <Row key={id} id={id} depth={depth + 1} />)}
        </>}
      </div>
    );
  }

  return (
    <div className="panel layers" data-testid="layers-panel">
      <div className="panel-title">Layers <span className="muted">(front at top · ▲▼ reorder)</span></div>
      <div className="layer-tree"><GroupView g={tree} depth={0} /></div>
      <div className="row gap">
        <button disabled={!selected} onClick={() => {
          const name = prompt("New group name", "Group");
          if (name && selected) onOp({ type: "group.create", id: name, name, parent: byId[selected].group, layers: [selected] });
        }}>Group selected</button>
        <button disabled={!selected} onClick={() => selected && onOp({ type: "layer.duplicate", id: selected })}>Duplicate</button>
      </div>
      <div className="legend">{Object.entries(CAT_COLOR).map(([k, c]) => <span key={k}><i style={{ background: c }} />{k}</span>)}</div>
    </div>
  );
}
