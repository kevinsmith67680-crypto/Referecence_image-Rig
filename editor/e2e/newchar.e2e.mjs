// Phase 3.8 end-to-end test of the AutoRig editor on the NEW character (tough_student_test).
// Runs the real FastAPI backend on a scratch copy of the package and drives the browser with Playwright. Every edit
// is checked through the persisted project state (API and files on disk), then the server is restarted and the
// project reopened. Nothing is written to the committed package.
//
//   cd editor && AUTORIG_PYTHON=../.venv/bin/python NODE_PATH=$(npm root -g) node --test e2e/newchar.e2e.mjs
// Env: AUTORIG_PYTHON, AUTORIG_PACKAGE (default ../projects/tough_student_test/character_package),
//      AUTORIG_SHOTS_P38 (default ../docs/phase3_8/screenshots).
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
let chromium;
try { ({ chromium } = require("playwright")); } catch {
  ({ chromium } = require(path.join(process.env.NODE_PATH || "/usr/lib/node_modules", "playwright")));
}
const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");
const PKG = process.env.AUTORIG_PACKAGE || path.join(repo, "projects/tough_student_test/character_package");
const PY = process.env.AUTORIG_PYTHON || "python3";
const SHOTS = process.env.AUTORIG_SHOTS_P38 || path.join(repo, "docs/phase3_8/screenshots");
const PORT = 8890 + Math.floor(Math.random() * 100);
const BASE = `http://127.0.0.1:${PORT}`;
const LIB = "tough_student_hands_v1";
mkdirSync(SHOTS, { recursive: true });

const sha = (f) => createHash("sha256").update(readFileSync(f)).digest("hex");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function startServer(pkg) {
  const proc = spawn(PY, ["-m", "autorig", "editor", pkg, "--port", String(PORT)], {
    cwd: repo, env: { ...process.env, PYTHONPATH: path.join(repo, "src") }, stdio: ["ignore", "pipe", "pipe"] });
  proc.stderr.on("data", (d) => process.stderr.write(`[server] ${d}`));
  return proc;
}
function stopServer(proc) {
  return new Promise((resolve) => {
    if (proc.exitCode !== null || proc.signalCode !== null) return resolve();
    proc.once("exit", () => resolve());
    proc.kill();
  });
}
async function waitUp() {
  for (let i = 0; i < 180; i++) {
    try { const r = await fetch(`${BASE}/api/project`); if (r.ok) return; } catch { /* starting */ }
    await sleep(500);
  }
  throw new Error("editor server did not start");
}
const api = async (p, opts) => (await fetch(`${BASE}${p}`, opts)).json();
const post = (p, body) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
async function toScreen(page, x, y) {
  return page.evaluate(([x, y]) => {
    const v = window.__autorigView;
    const r = document.querySelector("[data-testid=canvas]").getBoundingClientRect();
    return { x: r.left + x * v.scale + v.x, y: r.top + y * v.scale + v.y };
  }, [x, y]);
}
async function waitRev(page, before) {
  await page.waitForFunction((b) => !document.querySelector("[data-testid=revision]").textContent.includes(b),
    before, { timeout: 60000 });
}
const rev = async (page) => (await page.textContent("[data-testid=revision]")).split(" ")[1];
async function waitPose(page) {
  await page.waitForFunction(() => {
    const t = document.querySelector("[data-testid=pose-panel]")?.textContent ?? "";
    return /checks|confidence/.test(t) && !t.includes("rendering");
  }, null, { timeout: 120000 });
  await sleep(600);
}

const BIG = /natural_pose[\\/](poses|legacy|closeups|motion|garments|ik_switch|correctives|artistic|performance|hand_selection|reference_motion)([\\/]|$)/;

test("Phase 3.8: the editor works on a new character and its edits persist", { timeout: 1500000 }, async () => {
  const work = mkdtempSync(path.join(tmpdir(), "autorig-p38-"));
  const pkg = path.join(work, "character_package");
  cpSync(PKG, pkg, { recursive: true, filter: (s) => !s.includes(`${path.sep}editor`) && !BIG.test(s) });
  const srcFile = path.join(pkg, "source/original.jpg");
  const srcSha = sha(srcFile);
  const log = [];
  let server = startServer(pkg);
  const browser = await chromium.launch();
  try {
    await waitUp();
    const page = await browser.newPage({ viewport: { width: 1680, height: 1050 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    // 1. view the imported artwork
    await page.goto(BASE);
    await page.waitForSelector("[data-testid=layers-panel]");
    await page.waitForFunction(() => document.querySelector("[data-testid=reference]")?.complete);
    await sleep(2500);
    await page.screenshot({ path: path.join(SHOTS, "01_new_character_loaded.png") });
    let pl = await api("/api/project");
    let st = pl.state;
    assert.ok(st.layers.length >= 25, `layers loaded: ${st.layers.length}`);
    log.push(`1 view artwork: ${st.layers.length} layers`);

    // 2. edit a segmentation mask: move blazer pixels from the right upper-arm layer to the torso
    await page.click("[data-testid=layer-upper_arm_r]");
    await page.click("[data-testid=tab-Mask]");
    await page.selectOption("[data-testid=transfer-to]", "torso");
    await page.click("[data-testid=tool-polygon]");
    const sh = st.bones.upper_arm_r.head;
    const box = [[sh[0] + 30, sh[1] + 10], [sh[0] + 56, sh[1] + 10], [sh[0] + 56, sh[1] + 120], [sh[0] + 30, sh[1] + 120]];
    for (const [x, y] of box) {
      const p = await toScreen(page, x, y);
      await page.mouse.click(p.x, p.y);
      await sleep(450);
    }
    let r0 = await rev(page);
    await page.click("[data-testid=poly-sub]");
    await waitRev(page, r0);
    pl = await api("/api/project");
    assert.equal(pl.state.mask_ops.length, 1, "mask edit is a project op");
    assert.ok(pl.mask_validation.reassigned_px > 0, `pixels moved between layers: ${pl.mask_validation.reassigned_px}`);
    assert.equal(pl.mask_validation.unassigned_px, 0, "no neutral gap");
    const fid = (await api("/api/compare/metrics")).fidelity;
    assert.equal(fid.neutral_exact, true, "the neutral composite still equals the source");
    await page.screenshot({ path: path.join(SHOTS, "02_mask_edit.png") });
    log.push(`2 mask edit: ${pl.mask_validation.reassigned_px} px moved, neutral exact`);

    // 3. adjust a bone pivot (Joints tool)
    await page.click("[data-testid=tab-Joints]");
    await page.selectOption("[data-testid=bone-select]", "upper_arm_r");
    const h0 = pl.state.bones.upper_arm_r.head;
    let p = await toScreen(page, h0[0], h0[1]);
    const p2 = await toScreen(page, h0[0] + 10, h0[1] - 4);
    r0 = await rev(page);
    await page.mouse.move(p.x, p.y); await page.mouse.down(); await page.mouse.move(p2.x, p2.y, { steps: 8 }); await page.mouse.up();
    await waitRev(page, r0);
    pl = await api("/api/project");
    const h1 = pl.state.bones.upper_arm_r.head;
    assert.ok(Math.abs(h1[0] - (h0[0] + 10)) < 2.5 && Math.abs(h1[1] - (h0[1] - 4)) < 2.5, `pivot moved ${h0} -> ${h1}`);
    await page.screenshot({ path: path.join(SHOTS, "03_pivot_edit.png") });
    log.push(`3 pivot: ${h0} -> ${h1}`);

    // 4-5. mesh weights and garment parts (the crest is a rigid badge on the chest)
    await page.click("[data-testid=tab-Weights]");
    await page.waitForSelector("[data-testid=weights-panel]");
    await page.waitForFunction(() => /validation: OK/.test(document.querySelector("[data-testid=weights-panel]").textContent),
      null, { timeout: 240000 });
    await sleep(1500);
    await page.screenshot({ path: path.join(SHOTS, "04_garment_parts.png") });
    const nW = (await api("/api/project")).state.garment_weights.ops.length;
    await post("/api/op", { type: "weights.paint", layer: "torso", bone: "clavicle_r", center: [h1[0] + 70, h1[1] + 70],
      radius: 25.0, strength: 0.4 });
    assert.equal((await api("/api/project")).state.garment_weights.ops.length, nW + 1, "weight edit is a project op");
    const gi = await api("/api/garments");
    assert.ok(gi.available && gi.validation.ok, "garment rig and weight meshes valid");
    const badge = gi.structure.parts.find((x) => x.name === "badge");
    assert.ok(badge && badge.class === "RIGID", "crest detected as a rigid badge");
    const ex = await post("/api/garments/explain", { layer: "torso", x: Math.round(badge.centroid[0]), y: Math.round(badge.centroid[1]) });
    assert.equal(ex.part, "badge", JSON.stringify(ex).slice(0, 200));
    await page.selectOption("[data-testid=weights-view]", "influence");
    await page.selectOption("[data-testid=weights-bone]", "chest");
    await sleep(2000);
    await page.screenshot({ path: path.join(SHOTS, "05_weights_chest.png") });
    log.push(`4-5 weights: op stored; garment parts ${gi.structure.parts.length}; crest -> ${ex.part} (${ex.class})`);

    // 6. generated corrections are listed, all pending
    const ci = await api("/api/correctives");
    assert.ok(ci.assets.length >= 1, `corrective assets: ${ci.assets.length}`);
    assert.ok(ci.assets.every((a) => a.state === "pending_review"), "nothing approved automatically");
    await page.click("[data-testid=tab-Correctives]");
    await page.waitForSelector("[data-testid=correctives-table]", { timeout: 60000 });
    await page.screenshot({ path: path.join(SHOTS, "06_correctives.png") });
    log.push(`6 correctives: ${ci.assets.length} assets, all pending`);

    // 7. approve / reject hand drawings (user actions) and adjust a wrist offset
    await page.click("[data-testid=tab-Hands]");
    await page.waitForSelector("[data-testid=hand-library]", { timeout: 120000 });
    await page.selectOption("[data-testid=hand-side]", "r");
    const card = `[data-testid=hc-r-pointing-store_${LIB}_right_pointing]`;
    await page.waitForSelector(card, { timeout: 120000 });
    await page.click(card);
    await page.waitForSelector("[data-testid=hand-detail]");
    st = (await api("/api/project")).state;
    assert.notEqual(st.approvals[`handstore:${LIB}/right_pointing@v1`]?.status, "approved", "imported drawings start unapproved");
    r0 = await rev(page);
    await page.click("[data-testid=hand-approve]");
    await waitRev(page, r0);
    r0 = await rev(page);
    await page.click("[data-testid=hand-dxp]");
    await waitRev(page, r0);
    st = (await api("/api/project")).state;
    assert.equal(st.approvals[`handstore:${LIB}/right_pointing@v1`].status, "approved", "approval only by user action");
    assert.equal(st.hand_store.placement[`${LIB}/right_pointing`].dx, 1, "wrist offset stored per variant");
    await post("/api/op", { type: "approval.set", item: `handstore:${LIB}/left_fist@v1`, status: "rejected" });
    assert.equal((await api("/api/project")).state.approvals[`handstore:${LIB}/left_fist@v1`].status, "rejected");
    await sleep(1200);
    await page.screenshot({ path: path.join(SHOTS, "07_hand_approve_offset.png") });
    log.push("7 hands: right pointing approved (scratch copy), dx +1; left fist rejected");

    // 8. inspect hand metadata and the selection decision
    await page.click('[data-testid="tab-Hand Inspector"]');
    await page.waitForSelector("[data-testid=hi-active]", { timeout: 120000 });
    await page.selectOption("[data-testid=hi-mode]", "production");
    await page.click("[data-testid=hi-inspect]");
    await page.waitForFunction(() => /wrist checks/.test(document.querySelector("[data-testid=hand-inspector]")?.textContent || "") &&
      !/…/.test(document.querySelector("[data-testid=hi-inspect]")?.textContent || ""), null, { timeout: 180000 });
    assert.match(await page.textContent("[data-testid=hi-explanation]"), /mode: production/);
    const meta = await api("/api/hands/metadata");
    const assets = Array.isArray(meta.assets) ? meta.assets : Object.values(meta.assets || {});
    assert.ok(assets.length >= 16, `hand metadata assets: ${assets.length}`);
    await page.screenshot({ path: path.join(SHOTS, "08_hand_inspector.png") });
    log.push(`8 hand inspector: ${assets.length} metadata records`);

    // 9. switch a hand (manual override) and 10. preview poses
    await page.click("[data-testid=tab-Switches]");
    await page.waitForSelector("[data-testid=sw-hand_r]", { timeout: 120000 });
    r0 = await rev(page);
    await page.click("[data-testid=sw-hand_r-fist]");
    await waitRev(page, r0);
    assert.equal((await api("/api/project")).state.switches.hand_r.user, "fist");
    await page.click("[data-testid=tab-Pose]");
    await waitPose(page);
    await page.selectOption("[data-testid=preset]", "pointing_speaking");
    await waitPose(page);
    assert.match(await page.textContent("[data-testid=pose-panel]"), /SUPPORTED|NEEDS_ARTWORK|NEEDS_REVIEW/);
    await page.screenshot({ path: path.join(SHOTS, "09_pose_pointing.png") });
    log.push("9-10 switch + pose preview");

    // 11-12. animation curves and motion preview
    await page.click("[data-testid=tab-Performance]");
    await page.waitForSelector("[data-testid=performance-panel] [data-testid=perf-scenario-acting_upgrade]", { timeout: 60000 });
    r0 = await rev(page);
    await page.click("[data-testid=perf-scenario-acting_upgrade]");
    await waitRev(page, r0);
    await page.waitForSelector("[data-testid=perf-timeline]", { timeout: 240000 });
    await page.click("[data-testid=perf-key-k4]");
    await page.waitForSelector("[data-testid=perf-key-editor]");
    r0 = await rev(page);
    await page.selectOption("[data-testid=perf-curve-kind]", "bezier");
    await waitRev(page, r0);
    const sp = (await api("/api/project")).state.performance.doc;
    assert.equal(sp.base_keys.find((k) => k.id === "k4").curve.kind, "bezier", "curve edit stored");
    await page.click("[data-testid=perf-play]");
    await sleep(800);
    await page.click("[data-testid=perf-play]");
    await page.click("[data-testid=perf-render-frame]");
    await page.waitForFunction(() => { const im = document.querySelector("[data-testid=perf-frame-img]"); return im && im.complete && im.naturalWidth > 0; }, null, { timeout: 180000 });
    await page.screenshot({ path: path.join(SHOTS, "10_performance.png") });
    log.push("11-12 curve edit + playback + rendered frame");

    // 13. reference motion: select, retarget onto the new skeleton, use as performance
    await page.click('[data-testid="tab-Ref Motion"]');
    await page.waitForSelector("[data-testid=ref-synthetic_pointing]", { timeout: 120000 });
    r0 = await rev(page);
    await page.click("[data-testid=ref-synthetic_pointing]");
    await waitRev(page, r0);
    await page.click("[data-testid=ref-retarget]");
    await page.waitForSelector("[data-testid=ref-result]", { timeout: 240000 });
    const refText = await page.textContent("[data-testid=ref-result]");
    assert.match(refText, /SUPPORTED|REQUIRES/);
    await page.screenshot({ path: path.join(SHOTS, "11_reference_pointing.png") });
    log.push(`13 reference retarget: ${refText.slice(0, 80)}`);

    // 15. undo / redo
    const cur = (await api("/api/project")).project.cursor;
    r0 = await rev(page);
    await page.click("[data-testid=undo]");
    await waitRev(page, r0);
    assert.equal((await api("/api/project")).project.cursor, cur - 1);
    r0 = await rev(page);
    await page.click("[data-testid=redo]");
    await waitRev(page, r0);
    assert.equal((await api("/api/project")).project.cursor, cur);

    // 14 + 16. save a version, close (stop the server), reopen: every edit survives
    await api("/api/versions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label: "p38" }) });
    const saved = (await api("/api/project")).state;
    await stopServer(server);
    server = startServer(pkg);
    await waitUp();
    await page.reload();
    await page.waitForSelector("[data-testid=layers-panel]");
    const re = await api("/api/project");
    assert.deepEqual(re.state.bones.upper_arm_r.head, saved.bones.upper_arm_r.head, "pivot persists");
    assert.equal(re.state.mask_ops.length, 1, "mask edit persists");
    assert.equal(re.state.garment_weights.ops.length, saved.garment_weights.ops.length, "weights persist");
    assert.equal(re.state.approvals[`handstore:${LIB}/right_pointing@v1`].status, "approved", "approval persists");
    assert.equal(re.state.hand_store.placement[`${LIB}/right_pointing`].dx, 1, "wrist offset persists");
    assert.equal(re.state.switches.hand_r.user, "fist", "switch persists");
    assert.equal(re.state.performance.doc.base_keys.find((k) => k.id === "k4").curve.kind, "bezier", "curve persists");
    assert.equal(re.state.reference_motion.selected, "synthetic_pointing", "reference selection persists");
    assert.equal(re.project.versions.length, 1);
    assert.equal(sha(srcFile), srcSha, "the source image is never written");
    await page.screenshot({ path: path.join(SHOTS, "12_reopened.png") });
    log.push("14-16 save, restart, reopen: all edits persist; source unchanged");

    // export into the editor's exports folder (validated)
    const exr = await post("/api/export", {});
    assert.deepEqual(exr.validation_errors, [], JSON.stringify(exr).slice(0, 300));
    assert.equal(exr.production_ready, false, "never production-ready while approvals and artwork are missing");
    log.push(`export: ${exr.overall}, production_ready ${exr.production_ready}`);
    assert.deepEqual(errors, [], "no page errors");
    writeFileSync(path.join(SHOTS, "e2e_log.json"), JSON.stringify({ steps: log, export_overall: exr.overall }, null, 1));
  } finally {
    await browser.close();
    await stopServer(server);
    rmSync(work, { recursive: true, force: true });
  }
});
