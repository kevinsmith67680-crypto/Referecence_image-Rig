"""Human approval workflow: approvals.json + a static local review page.

Nothing generated is ever created as 'approved'. Decisions are applied with
`autorig review` (or by importing the JSON the HTML page exports). The page is
self-contained (no network, no server): open reports/visual_review.html.
"""

from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Any

from .store import ArtworkStore

DECISIONS = ("approve", "reject", "request_candidate")


def build_items(store: ArtworkStore, vectors: list[dict], joints_prep: dict, occlusion_edges: list[dict],
                switch_slots: list[dict]) -> list[dict[str, Any]]:
    items = []
    for e in store.extensions:
        items.append({"id": e.id, "type": "generated_extension", "component": e.component, "method": e.method,
                      "region": e.region_id, "pixels": e.pixels, "status": e.status,
                      "images": {"extension": e.image, "provenance_mask": e.provenance_mask,
                                 "change_map": f"reports/change_maps/{e.component}.png"}})
    for v in vectors:
        items.append({"id": v["id"], "type": v["kind"], "component": v["target"], "path": v["path"],
                      "status": v["status"]})
    for j, info in joints_prep.items():
        items.append({"id": f"pivot:{j}", "type": "joint_pivot", "pivot": info["pivot"],
                      "status": "needs_review" if not info["pivot_reliable"] else "pending_review",
                      "issues": info["issues"]})
    for e in occlusion_edges:
        if e["ambiguous"]:
            items.append({"id": f"edge:{e['front']}>{e['back']}", "type": "occlusion_edge", "status": "needs_review",
                          "confidence": e["confidence"], "method": e["method"]})
    for s in switch_slots:
        items.append({"id": f"switch:{s['id']}", "type": "missing_switch_drawing", "status": "missing",
                      "description": s["description"]})
    return items


def write_approvals(root: Path, items: list[dict]) -> dict:
    doc = {"schema": "autorig.approvals", "schema_version": "1.0.0", "items": items, "decisions": [],
           "phase_approval": {"approved_for_next_phase": False, "approved_at": None,
                              "rule": "requires every generated item approved or rejected and no needs_review left"}}
    (root / "reports" / "approvals.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def apply_decision(root: Path, item_id: str, decision: str, note: str = "", **kw) -> dict:
    path = root / "reports" / "approvals.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    item = next((i for i in doc["items"] if i["id"] == item_id), None)
    if item is None:
        raise KeyError(f"unknown review item {item_id}")
    store = ArtworkStore(root, *json.loads((root / "artwork" / "store.json").read_text())["canvas"])
    if decision == "approve":
        if item["type"] == "missing_switch_drawing":
            raise ValueError("a missing drawing cannot be approved; provide the artwork first")
        item["status"] = "approved"
        if item["type"] == "generated_extension":
            store.set_status(item_id, "approved", note)
    elif decision == "reject":
        item["status"] = "rejected"
        if item["type"] == "generated_extension":
            store.set_status(item_id, "rejected", note)
    elif decision == "request_candidate":
        item["status"] = "candidate_requested"
    elif decision == "restore_original":
        removed = store.rollback(item_id)
        for i in doc["items"]:
            if i["type"] == "generated_extension" and i["component"] == item_id:
                i["status"] = "rolled_back"
        item = {"id": item_id, "removed_extensions": removed}
    elif decision == "set_pivot":
        item["pivot"] = kw["xy"]
        item["status"] = "corrected"
        item["previous_pivot"] = kw.get("previous")
    elif decision == "set_region_mask":
        item["status"] = "mask_adjusted"
        item["replacement_mask"] = kw["mask_path"]
    else:
        raise ValueError(decision)
    store.save()
    doc["decisions"].append({"item": item_id, "decision": decision, "note": note, "at": time.time(), **kw})
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return item


def approve_phase(root: Path) -> dict:
    path = root / "reports" / "approvals.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    open_items = [i["id"] for i in doc["items"] if i["status"] in ("pending_review", "needs_review", "candidate_requested")]
    if open_items:
        raise ValueError(f"cannot approve for the next phase: {len(open_items)} item(s) unreviewed, e.g. {open_items[:5]}")
    doc["phase_approval"] = {**doc["phase_approval"], "approved_for_next_phase": True, "approved_at": time.time()}
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc["phase_approval"]


def write_review_html(root: Path, items: list[dict], images: dict[str, str], summary: dict[str, Any]) -> Path:
    def img(rel, label):
        return (f'<figure><img src="../{html.escape(rel)}" loading="lazy" alt="{html.escape(label)}">'
                f'<figcaption>{html.escape(label)}</figcaption></figure>')
    rows = []
    for it in items:
        extra = ""
        if it["type"] == "generated_extension":
            extra = img(it["images"]["extension"], "generated pixels") + img(it["images"]["change_map"], "change map")
        opts = "".join(f'<label><input type="radio" name="{html.escape(it["id"])}" value="{d}"> {d}</label>'
                       for d in DECISIONS) if it["status"] not in ("missing",) else "<em>artwork missing</em>"
        rows.append(f'<tr><td><code>{html.escape(it["id"])}</code><br><small>{html.escape(it["type"])}</small></td>'
                    f'<td>{html.escape(str(it["status"]))}</td><td class="imgs">{extra}</td><td>{opts}</td></tr>')
    gallery = "".join(img(rel, label) for label, rel in images.items())
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoRig review</title><style>
:root{{--bg:#fafafa;--fg:#1b1b1b;--line:#ddd;--accent:#b00060}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151515;--fg:#eee;--line:#333;--accent:#ff4fa3}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;margin:0 16px 40px}}
h1{{font-size:1.4rem}} .warn{{border-left:4px solid var(--accent);padding:8px 12px}}
.gallery{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}}
figure{{margin:0}} img{{max-width:100%;background:repeating-conic-gradient(#ccc 0 25%,#fff 0 50%) 0/16px 16px;border:1px solid var(--line)}}
table{{border-collapse:collapse;width:100%}} td{{border-top:1px solid var(--line);padding:8px;vertical-align:top}}
td.imgs figure{{display:inline-block;width:160px;margin-right:8px}} label{{display:block}}
button{{font:inherit;padding:8px 14px;margin:12px 0}} pre{{white-space:pre-wrap}}
</style></head><body>
<h1>AutoRig Studio: reconstruction review</h1>
<p class="warn"><strong>Magenta = generated pixels.</strong> Nothing below is production-approved until you approve it.
Synthetic-fixture results do not prove anything about real artwork.</p>
<pre>{html.escape(json.dumps(summary, indent=2))}</pre>
<h2>Previews</h2><div class="gallery">{gallery}</div>
<h2>Items</h2><table>{''.join(rows)}</table>
<button id="export">Export decisions JSON</button><pre id="out"></pre>
<p>Apply with: <code>python -m autorig review &lt;project&gt; --import decisions.json</code></p>
<script>
document.getElementById('export').onclick=()=>{{
 const d=[...document.querySelectorAll('input[type=radio]:checked')].map(i=>({{item:i.name,decision:i.value}}));
 const blob=new Blob([JSON.stringify({{decisions:d}},null,2)],{{type:'application/json'}});
 const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='decisions.json';a.click();
 document.getElementById('out').textContent=JSON.stringify(d,null,2);}};
</script></body></html>"""
    out = root / "reports" / "visual_review.html"
    out.write_text(page, encoding="utf-8")
    return out
