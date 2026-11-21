// End-to-end test of the AutoRig editor on the user's real T-pose character package.
// Runs the real FastAPI backend on a scratch copy of the package, drives the browser with Playwright,
// and checks every edit through the persisted state (API and files on disk), not just the UI.
//
//   cd editor && npm run build && npm run e2e
// Env: AUTORIG_PYTHON (python with autorig installed, default "python3"), AUTORIG_PACKAGE (package dir),
//      AUTORIG_SHOTS (screenshot dir, default ../docs/phase3/screenshots).
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
const PKG = process.env.AUTORIG_PACKAGE || path.join(repo, "projects/schoolboy_tpose/character_package");
const PY = process.env.AUTORIG_PYTHON || "python3";
const SHOTS = process.env.AUTORIG_SHOTS || path.join(repo, "docs/phase3/screenshots");
const SHOTS32 = process.env.AUTORIG_SHOTS_P32 || path.join(repo, "docs/phase3_2/screenshots");
const SHOTSHR = process.env.AUTORIG_SHOTS_HR || path.join(repo, "docs/hand_replacement/screenshots");
const SHOTS33 = process.env.AUTORIG_SHOTS_P33 || path.join(repo, "docs/phase3_3/screenshots");
const PORT = 8790 + Math.floor(Math.random() * 100);
const BASE = `http://127.0.0.1:${PORT}`;
mkdirSync(SHOTS, { recursive: true });
mkdirSync(SHOTS32, { recursive: true });
mkdirSync(SHOTS33, { recursive: true });

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
  for (let i = 0; i < 120; i++) {
    try { const r = await fetch(`${BASE}/api/project`); if (r.ok) return; } catch { /* starting */ }
    await sleep(500);
  }
  throw new Error("editor server did not start");
}
const api = async (p, opts) => (await fetch(`${BASE}${p}`, opts)).json();

async function toScreen(page, x, y) {
  return page.evaluate(([x, y]) => {
    const v = window.__autorigView;
    const r = document.querySelector("[data-testid=canvas]").getBoundingClientRect();
    return { x: r.left + x * v.scale + v.x, y: r.top + y * v.scale + v.y };
  }, [x, y]);
}
async function waitRev(page, before) {
  await page.waitForFunction((b) => !document.querySelector("[data-testid=revision]").textContent.includes(b),
    before, { timeout: 30000 });
}
const rev = async (page) => (await page.textContent("[data-testid=revision]")).split(" ")[1];
async function waitIK(page) {
  await page.waitForFunction(() => {
    const t = document.querySelector("[data-testid=ik-timing]")?.textContent ?? "";
    return /full frame/.test(t) && !t.includes("rendering");
  }, null, { timeout: 90000 });
  await sleep(600);
}
const post = (p, body) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
async function drag(page, from, to, steps = 12, pauseMs = 0) {
  // pauseMs > 0: a human-paced drag (one move every pauseMs), so the skeleton previews can be measured
  const a = await toScreen(page, from[0], from[1]);
  const b = await toScreen(page, to[0], to[1]);
  await page.mouse.move(a.x, a.y); await page.mouse.down();
  if (!pauseMs) await page.mouse.move(b.x, b.y, { steps });
  else for (let i = 1; i <= steps; i++) {
    await page.mouse.move(a.x + (b.x - a.x) * i / steps, a.y + (b.y - a.y) * i / steps);
    await sleep(pauseMs);
  }
  await page.mouse.up();
}
async function waitPose(page) {
  await page.waitForFunction(() => {
    const t = document.querySelector("[data-testid=pose-panel]")?.textContent ?? "";
    return /checks|confidence/.test(t) && !t.includes("rendering");
  }, null, { timeout: 90000 });
  await sleep(600);
}

test("editor: real edits persist, poses preview, package exports", { timeout: 600000 }, async () => {
  const work = mkdtempSync(path.join(tmpdir(), "autorig-e2e-"));
  const pkg = path.join(work, "character_package");
  cpSync(PKG, pkg, { recursive: true, filter: (s) => !s.includes(`${path.sep}editor`) && !s.includes(`${path.sep}hand_store`) &&
    !/natural_pose[\\/](poses|legacy|closeups)/.test(s) });
  const srcFile = path.join(pkg, "source/original.jpg");
  const srcSha = existsSync(srcFile) ? sha(srcFile) : sha(path.join(pkg, "source/original.png"));
  let server = startServer(pkg);
  const browser = await chromium.launch();
  try {
    await waitUp();
    const page = await browser.newPage({ viewport: { width: 1680, height: 1050 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.goto(BASE);
    await page.waitForSelector("[data-testid=layers-panel]");
    await page.waitForFunction(() => document.querySelector("[data-testid=reference]")?.complete);
    await sleep(2500);
    await page.screenshot({ path: path.join(SHOTS, "01_loaded.png") });
    let st = (await api("/api/project")).state;
    assert.ok(st.layers.length >= 25, "real project layers loaded");

    // 1. pixel-accurate selection on the canvas: click the right forearm (image coords)
    const fa = st.bones.forearm_r.head;
    let p = await toScreen(page, fa[0] - 40, fa[1]);
    await page.mouse.click(p.x, p.y);
    await page.waitForFunction(() => document.querySelector("[data-testid=properties]") !== null);
    const selText = await page.textContent("footer");
    assert.match(selText, /layer forearm_r/, "canvas click selects the layer under the cursor");

    // 2. reorder and visibility (persisted)
    const before = st.layers.findIndex((l) => l.id === "forearm_r");
    let r0 = await rev(page);
    await page.click("[data-testid=up-forearm_r]");
    await waitRev(page, r0);
    st = (await api("/api/project")).state;
    assert.equal(st.layers.findIndex((l) => l.id === "forearm_r"), before + 1, "layer moved one step toward the front");
    // Moving the forearm in front of the hand puts its reconstructed (hidden-part) extension over the hand's
    // original pixels: the fidelity check must catch that, and the diff heatmap must show it.
    let fid = (await api("/api/compare/metrics")).fidelity;
    assert.equal(fid.neutral_exact, false, "z-order change that covers original pixels is detected");
    assert.ok(fid.max_abs_error_in_covered > 0);
    await page.click("[data-testid=tab-Compare]");
    await page.click("[data-testid=cmp-diff]");
    await page.waitForFunction(() => /Neutral fidelity\s*differs/.test(document.querySelector("[data-testid=compare-panel]")?.textContent ?? ""));
    await page.waitForFunction(() => document.querySelector("[data-testid=readiness]").textContent.includes("FAIL"), null, { timeout: 60000 });
    const g9 = (await api("/api/gates")).gates.find((g) => g.id === 9);
    assert.equal(g9.level, "FAIL", "original-preservation gate fails while generated pixels cover originals");
    const wr = st.bones.hand_r ? st.bones.hand_r.head : st.bones.forearm_r.tail;   // zoom to the right wrist
    p = await toScreen(page, wr[0], wr[1]);
    await page.mouse.move(p.x, p.y);
    for (let i = 0; i < 10; i++) { await page.mouse.wheel(0, -100); await sleep(50); }
    await sleep(1500);
    await page.screenshot({ path: path.join(SHOTS, "02_reorder_fidelity_diff.png") });
    await page.keyboard.press("f");
    r0 = await rev(page);
    await page.click("[data-testid=down-forearm_r]");
    await waitRev(page, r0);
    fid = (await api("/api/compare/metrics")).fidelity;
    assert.equal(fid.neutral_exact, true, "moving the layer back restores the exact neutral composite");
    await page.click("[data-testid=cmp-split]");
    await page.click("[data-testid=tab-Properties]");
    r0 = await rev(page);
    await page.click("[data-testid=vis-hair]");
    await waitRev(page, r0);
    st = (await api("/api/project")).state;
    assert.equal(st.layers.find((l) => l.id === "hair").visible, false);
    r0 = await rev(page);
    await page.click("[data-testid=vis-hair]");
    await waitRev(page, r0);

    // 3. mask correction: move the blazer pixels that SAM 3 put in the right upper-arm layer to the torso
    await page.click("[data-testid=layer-upper_arm_r]");
    await page.click("[data-testid=tab-Mask]");
    await page.selectOption("[data-testid=transfer-to]", "torso");
    await page.click("[data-testid=tool-polygon]");
    for (const [x, y] of [[428, 402], [470, 402], [470, 584], [428, 584]]) {
      p = await toScreen(page, x, y);
      await page.mouse.click(p.x, p.y);
      await sleep(450); // stay outside the double-click window (double-click closes the polygon)
    }
    r0 = await rev(page);
    await page.click("[data-testid=poly-sub]");
    await waitRev(page, r0);
    let pl = await api("/api/project");
    assert.equal(pl.state.mask_ops.length, 1);
    assert.ok(pl.mask_validation.reassigned_px > 1000, "blazer pixels moved to the torso");
    assert.equal(pl.mask_validation.unassigned_px, 0, "no neutral gap created");
    await sleep(1200);
    await page.screenshot({ path: path.join(SHOTS, "03_mask_correction.png") });
    // brush: add a few pixels back (soft brush)
    await page.click("[data-testid=tool-brush]");
    p = await toScreen(page, 445, 470);
    const q = await toScreen(page, 455, 480);
    r0 = await rev(page);
    await page.mouse.move(p.x, p.y); await page.mouse.down(); await page.mouse.move(q.x, q.y, { steps: 5 }); await page.mouse.up();
    await waitRev(page, r0);
    pl = await api("/api/project");
    assert.equal(pl.state.mask_ops.length, 2);
    assert.equal(pl.state.mask_ops[1].shape.type, "stroke");

    // 4. joint pivot drag (Joint tool) -> bone state + warnings
    await page.click("[data-testid=tab-Joints]");
    await page.selectOption("[data-testid=bone-select]", "upper_arm_r");
    const h0 = pl.state.bones.upper_arm_r.head;
    p = await toScreen(page, h0[0], h0[1]);
    const p2 = await toScreen(page, h0[0] + 12, h0[1] - 4);
    r0 = await rev(page);
    await page.mouse.move(p.x, p.y); await page.mouse.down(); await page.mouse.move(p2.x, p2.y, { steps: 8 }); await page.mouse.up();
    await waitRev(page, r0);
    pl = await api("/api/project");
    const h1 = pl.state.bones.upper_arm_r.head;
    assert.ok(Math.abs(h1[0] - (h0[0] + 12)) < 2.5 && Math.abs(h1[1] - (h0[1] - 4)) < 2.5, `pivot moved: ${h0} -> ${h1}`);
    assert.equal(pl.state.bones.upper_arm_r.edited, true);
    await sleep(800);
    await page.screenshot({ path: path.join(SHOTS, "04_joint_editor.png") });

    // 5. vector correction: create at the selected joint, drag an anchor
    await page.click("[data-testid=tab-Vectors]");
    r0 = await rev(page);
    await page.click("[data-testid=vec-new]");
    await waitRev(page, r0);
    pl = await api("/api/project");
    const vid = Object.keys(pl.state.vectors).find((k) => k.startsWith("shape_"));
    const n0 = pl.state.vectors[vid].nodes[0];
    await sleep(500);
    p = await toScreen(page, n0.x, n0.y);
    const p3 = await toScreen(page, n0.x + 15, n0.y + 6);
    r0 = await rev(page);
    await page.mouse.move(p.x, p.y); await page.mouse.down(); await page.mouse.move(p3.x, p3.y, { steps: 8 }); await page.mouse.up();
    await waitRev(page, r0);
    pl = await api("/api/project");
    const n1 = pl.state.vectors[vid].nodes[0];
    assert.ok(Math.abs(n1.x - (n0.x + 15)) < 2.5 && Math.abs(n1.y - (n0.y + 6)) < 2.5, "vector anchor moved");
    await sleep(500);
    await page.screenshot({ path: path.join(SHOTS, "05_vector_edit.png") });

    // 6. pose preview: preset + direct rotation, correction metadata, defect review
    await page.click("[data-testid=tab-Pose]");
    await waitPose(page);
    await page.screenshot({ path: path.join(SHOTS, "06_pose_relaxed.png") });
    await page.selectOption("[data-testid=preset]", "both_hands_raised");
    await waitPose(page);
    await page.screenshot({ path: path.join(SHOTS, "07_pose_arms_raised.png") });
    await page.selectOption("[data-testid=preset]", "relaxed_standing");
    await waitPose(page);
    await page.click("text=Direct rotations (manual)");
    await page.locator("[data-testid=rot-head]").fill("14");
    await waitPose(page);
    const poseText = await page.textContent("[data-testid=pose-panel]");
    assert.match(poseText, /SUPPORTED_WITH_CORRECTIONS|SUPPORTED|NEEDS_ARTWORK/);
    await page.selectOption("[data-testid=corr-joint]", "upper_arm_r");
    r0 = await rev(page);
    await page.click("[data-testid=corr-save-pose]");
    await waitRev(page, r0);
    r0 = await rev(page);
    await page.click("[data-testid=defect-accepted]");
    await waitRev(page, r0);
    pl = await api("/api/project");
    assert.ok(pl.state.corrections.joints.upper_arm_r.per_pose.relaxed_standing.band, "pose-specific correction saved");
    assert.equal(pl.state.defects["relaxed_standing:upper_arm_r"].status, "accepted");
    await page.check("[data-testid=boundaries]");
    await waitPose(page);
    await page.screenshot({ path: path.join(SHOTS, "08_pose_joint_correction.png") });

    // 6b. Phase 3.1 motion: gait phase, playback (measured fps), diagnostics, before/after, pending preview, close-up
    await page.selectOption("[data-testid=preset]", "running");
    await page.waitForSelector("[data-testid=gait-controls]");
    await page.locator("[data-testid=gait-phase]").fill("0.375");
    await page.waitForFunction(() => /SUPPORTED_WITH_CORRECTIONS/.test(
      document.querySelector("[data-testid=feasibility]")?.textContent ?? ""), null, { timeout: 90000 });
    await waitPose(page);
    await page.screenshot({ path: path.join(SHOTS, "14_run_cycle_flight.png") });
    for (const d of ["skeleton", "contacts", "com", "garments"]) await page.check(`[data-testid=diag-${d}]`);
    await waitPose(page);
    await page.screenshot({ path: path.join(SHOTS, "15_run_diagnostics.png") });
    for (const d of ["skeleton", "contacts", "com", "garments"]) await page.uncheck(`[data-testid=diag-${d}]`);
    await page.click("[data-testid=gait-play]");
    await sleep(4000);
    const perfText = await page.textContent("[data-testid=perf]");
    await page.screenshot({ path: path.join(SHOTS, "16_run_playback_skeleton.png") });
    await page.click("[data-testid=gait-play]");
    const fps = +(perfText.match(/(\d+) fps/) || [0, 0])[1];
    assert.ok(fps > 0, `playback measured: ${perfText}`);
    console.log(`# skeleton playback in headless Chromium (cloud CPU): ${perfText}`);
    await waitPose(page);
    await page.check("[data-testid=allow-pending]");
    await page.waitForFunction(() => /NEEDS_ARTWORK[\s\S]*hand:fist \(pending_review\)/.test(
      document.querySelector("[data-testid=feasibility]")?.textContent ?? ""), null, { timeout: 90000 });
    await waitPose(page);
    await page.screenshot({ path: path.join(SHOTS, "17_run_fists_pending_preview.png") });
    await page.uncheck("[data-testid=allow-pending]");
    await page.check("[data-testid=before-after]");
    await waitPose(page);
    await page.check("[data-testid=show-before]");
    await sleep(800);
    await page.screenshot({ path: path.join(SHOTS, "18_run_phase3_before.png") });
    await page.uncheck("[data-testid=before-after]");
    await waitPose(page);
    await page.click("[data-testid=close-shin_l]");
    await sleep(800);
    await page.screenshot({ path: path.join(SHOTS, "19_closeup_knee.png") });
    await page.keyboard.press("f");
    // animated cycle with the full artwork (fluid motion), played back in the canvas; downloads are served
    await page.selectOption("[data-testid=cycle-frames]", "12");
    await page.click("[data-testid=cycle-render]");
    await page.waitForFunction(() => /ready/.test(document.querySelector("[data-testid=cycle-progress]")?.textContent ?? ""),
      null, { timeout: 240000 });
    await page.waitForSelector("[data-testid=cycle-play]");
    await sleep(700);
    await page.screenshot({ path: path.join(SHOTS, "20_animated_cycle_frame.png") });
    const gifOk = await page.evaluate(async () => (await fetch(document.querySelector("a[href$='.gif']").href)).ok);
    assert.ok(gifOk, "animated GIF download");
    await page.click("[data-testid=cycle-play]");
    await page.selectOption("[data-testid=preset]", "relaxed_standing");
    await waitPose(page);

    // 6c. Phase 3.2: FK->IK matching, wrist and ankle targets dragged on the canvas, foot lock, hand switches
    await page.click("[data-testid=tab-IK]");
    await page.waitForSelector("[data-testid=ik-arm_r]");
    await waitIK(page);
    r0 = await rev(page);
    await page.click("[data-testid=ik-arm_r-ik]");
    await waitRev(page, r0);
    st = (await api("/api/project")).state;
    assert.equal(st.ik.arm_r.mode, "ik", "right arm switched to IK");
    const t0 = st.ik.arm_r.target;
    await waitIK(page);
    const goal = [t0[0] - 110, t0[1] - 260];
    r0 = await rev(page);
    await drag(page, t0, goal, 30, 60);
    await waitRev(page, r0);
    st = (await api("/api/project")).state;
    assert.ok(Math.hypot(st.ik.arm_r.target[0] - goal[0], st.ik.arm_r.target[1] - goal[1]) < 4, `wrist target moved: ${st.ik.arm_r.target}`);
    const lat = await page.evaluate(() => window.__autorigIkLatency ?? []);
    assert.ok(lat.length >= 5, `skeleton previews while dragging (${lat.length})`);
    const ls = [...lat].sort((a, b) => a - b);
    console.log(`# IK drag input-to-skeleton latency (headless Chromium, cloud CPU): ${lat.length} updates in 1.8 s, ` +
      `median ${ls[Math.floor(ls.length / 2)].toFixed(1)} ms, p90 ${ls[Math.floor(ls.length * 0.9)].toFixed(1)} ms, max ${ls[ls.length - 1].toFixed(1)} ms`);
    writeFileSync(path.join(SHOTS32, "ik_drag_latency_ms.json"), JSON.stringify({ samples: lat, note: "headless Chromium, cloud CPU; pointer move to skeleton-only response" }, null, 1));
    await waitIK(page);
    await page.screenshot({ path: path.join(SHOTS32, "01_ik_drag_wrist.png") });
    let pr = await post("/api/pose", { preset: "relaxed_standing", skeleton_only: true });
    assert.ok(["ok", "clamped_max_reach"].includes(pr.ik.limbs.arm_r.status), pr.ik.limbs.arm_r.status);
    if (pr.ik.limbs.arm_r.status === "ok") assert.ok(pr.ik.limbs.arm_r.error_px < 0.5, "wrist reaches its target");
    // left leg: IK, unlock the foot, lift the ankle target; the right foot stays planted
    r0 = await rev(page);
    await page.click("[data-testid=ik-leg_l-ik]");
    await waitRev(page, r0);
    r0 = await rev(page);
    await page.click("[data-testid=ik-leg_l-lock]");  // controlled checkbox: it flips when the op returns
    await waitRev(page, r0);
    st = (await api("/api/project")).state;
    const a0 = st.ik.leg_l.target;
    await waitIK(page);
    r0 = await rev(page);
    await drag(page, a0, [a0[0] + 10, a0[1] - 70]);
    await waitRev(page, r0);
    st = (await api("/api/project")).state;
    assert.ok(Math.abs(st.ik.leg_l.target[1] - (a0[1] - 70)) < 4, "ankle target lifted");
    pr = await post("/api/pose", { preset: "relaxed_standing", skeleton_only: true });
    assert.equal(pr.ik.contacts.l.state, "FREE");
    assert.equal(pr.ik.contacts.r.state, "PLANTED");
    assert.ok(pr.ik.contacts.r.lock_error_px < 0.05, "planted foot does not slide");
    await waitIK(page);
    await page.screenshot({ path: path.join(SHOTS32, "02_ik_drag_ankle_lifted.png") });
    // switches: a pending fist is only previewed; a missing grip is NEEDS_ARTWORK
    await page.click("[data-testid=tab-Switches]");
    await page.waitForSelector("[data-testid=sw-hand_r-fist]");
    r0 = await rev(page);
    await page.click("[data-testid=sw-hand_r-fist]");
    await waitRev(page, r0);
    await page.waitForFunction(() => /pending review/.test(document.querySelector("[data-testid=sw-hand_r]")?.textContent ?? ""),
      null, { timeout: 90000 });
    assert.equal(await page.textContent("[data-testid=sw-hand_r-active]"), "open_palm", "pending fist not shown as approved");
    await page.screenshot({ path: path.join(SHOTS32, "03_switch_pending_fist.png") });
    await page.click("[data-testid=tab-Pose]");
    await page.check("[data-testid=allow-pending]");
    await page.click("[data-testid=tab-Switches]");
    await page.waitForFunction(() => document.querySelector("[data-testid=sw-hand_r-active]")?.textContent === "fist",
      null, { timeout: 90000 });
    await sleep(2500);
    await page.screenshot({ path: path.join(SHOTS32, "04_switch_fist_preview_ik.png") });
    r0 = await rev(page);
    await page.click("[data-testid=sw-hand_r-align]");
    await waitRev(page, r0);
    st = (await api("/api/project")).state;
    assert.ok(st.switches.hand_r.offsets.fist, "alignment offset stored");
    r0 = await rev(page);
    await page.click("[data-testid=sw-hand_l-gripping]");
    await waitRev(page, r0);
    await page.waitForFunction(() => /NEEDS_ARTWORK/.test(document.querySelector("[data-testid=sw-hand_l]")?.textContent ?? ""),
      null, { timeout: 90000 });
    await page.screenshot({ path: path.join(SHOTS32, "05_switch_missing_grip.png") });
    r0 = await rev(page);
    await page.click("[data-testid=act-HAND_LEFT_POINT]");
    await waitRev(page, r0);
    st = (await api("/api/project")).state;
    assert.equal(st.switches.hand_l.user, "pointing", "live action -> switch op");
    const rc = await api("/api/rig/controls");
    assert.deepEqual(rc.validation_errors, [], "rig controls validate");
    await page.click("[data-testid=tab-Pose]");
    await page.uncheck("[data-testid=allow-pending]");
    await waitPose(page);

    // 6d. Hand Pose Store: import the user's pack in the UI, review and align a drawing, preview it posed
    const UIDL = "schoolboy_hand_switches_v1/left_relaxed";
    await page.click("[data-testid=tab-Hands]");
    await page.waitForSelector("[data-testid=hand-library]");
    await page.setInputFiles("[data-testid=hand-import]", path.join(repo, "references/schoolboy/hand_pack/AutoRig_Hand_Pose_Library.zip"));
    await page.waitForFunction(() => /imported/.test(document.querySelector("[data-testid=hand-import-result]")?.textContent ?? ""),
      null, { timeout: 180000 });
    const card = "[data-testid=hc-l-relaxed-store_schoolboy_hand_switches_v1_left_relaxed]";
    await page.waitForSelector(card, { timeout: 180000 });
    assert.ok(existsSync(path.join(pkg, "hand_store", "index.json")), "store persisted on disk");
    const badPack = await post("/api/handstore/import", { name: "bad.zip", data_b64: Buffer.from("nope").toString("base64") });
    assert.equal(badPack.status, "rejected");
    await page.selectOption("[data-testid=hand-side]", "l");
    await page.click(card);
    await page.waitForSelector("[data-testid=hand-detail]");
    await sleep(1500);
    await page.screenshot({ path: path.join(SHOTS32, "06_hand_library_candidate.png") });
    st = (await api("/api/project")).state;
    assert.notEqual(st.approvals[`handstore:${UIDL}@v1`]?.status, "approved", "imported drawings start unapproved");
    r0 = await rev(page);
    await page.click("[data-testid=hand-approve]");
    await waitRev(page, r0);
    r0 = await rev(page);
    await page.click("[data-testid=hand-dxp]");
    await waitRev(page, r0);
    st = (await api("/api/project")).state;
    assert.equal(st.approvals[`handstore:${UIDL}@v1`].status, "approved");
    assert.equal(st.hand_store.placement[UIDL].dx, 1);
    await page.click("[data-testid=hand-preview-pose]");
    await page.waitForFunction(() => /shown on the canvas/.test(document.querySelector("[data-testid=hand-preview-status]")?.textContent ?? ""),
      null, { timeout: 120000 });
    await sleep(800);
    await page.screenshot({ path: path.join(SHOTS32, "07_hand_preview_in_pose.png") });
    // the earlier manual choice (left hand: pointing) is kept; in auto mode the approved relaxed drawing is used
    pr = await post("/api/pose", { preset: "relaxed_standing", skeleton_only: true });
    assert.equal(pr.hands.l.source, "user", "auto-selection never overrides a manual choice");
    await post("/api/op", { type: "switch.set", group: "hand_l", variant: null, auto: true });
    pr = await post("/api/pose", { preset: "relaxed_standing", skeleton_only: true });
    assert.equal(pr.hands.l.cid, `store:${UIDL}`, "approved relaxed drawing chosen automatically");
    assert.equal(pr.hands.l.pending_review, false);
    // delete and restore a candidate, undo/redo through the op log
    await post("/api/op", { type: "handstore.delete", uid: "schoolboy_hand_switches_v1/left_cupped" });
    assert.ok((await api("/api/project")).state.hand_store.deleted.includes("schoolboy_hand_switches_v1/left_cupped"));
    await post("/api/undo");
    assert.ok(!(await api("/api/project")).state.hand_store.deleted.includes("schoolboy_hand_switches_v1/left_cupped"));

    // 6e. Sleeve-free hand replacement: import the new library (the sleeve-attached one is archived, rollback kept),
    // adjust a drawing's wrist pivot and overlap, read the selection engine's decision, set a gesture intention,
    // auto toggle / reset to automatic, archive and restore the new library
    mkdirSync(SHOTSHR, { recursive: true });
    const NEW = "schoolboy_hands_sleeveless";
    await page.click("[data-testid=tab-Hands]");
    await page.waitForSelector("[data-testid=hand-library]");
    await page.setInputFiles("[data-testid=hand-import]", path.join(repo, "references/schoolboy/hand_pack_v2/AutoRig_Hand_Library_Sleeveless_v2.zip"));
    await page.waitForFunction(() => /imported/.test(document.querySelector("[data-testid=hand-import-result]")?.textContent ?? ""),
      null, { timeout: 180000 });
    await page.waitForSelector(`[data-testid=hand-lib-${NEW}]`, { timeout: 180000 });
    assert.match(await page.textContent("[data-testid=hand-lib-schoolboy_hand_switches_v1]"), /archived/, "old library archived");
    assert.match(await page.textContent(`[data-testid=hand-lib-${NEW}]`), /active/);
    assert.match(await page.textContent("[data-testid=hand-lib-phase2_sheet_hands]"), /archived/, "sheet hands archived");
    await page.screenshot({ path: path.join(SHOTSHR, "01_libraries_archived_and_active.png") });
    const sel = `[data-testid=hc-r-pointing-store_${NEW}_right_pointing]`;
    await page.selectOption("[data-testid=hand-side]", "r");
    await page.waitForSelector(sel, { timeout: 120000 });
    await page.click(sel);
    await page.waitForSelector("[data-testid=hand-wrist-params]");
    assert.match(await page.textContent("[data-testid=hand-flags]"), /closed outline/, "estimated pivot flagged for review");
    const opClick = async (selector) => {   // an op through the UI, then wait until the project revision changes
      const r0 = await rev(page);
      await page.click(selector);
      await page.waitForFunction((b) => !document.querySelector("[data-testid=revision]").textContent.includes(b),
        r0, { timeout: 180000 });
    };
    await opClick("[data-testid=hand-pivot_xp]");
    await opClick("[data-testid=hand-overlapp]");
    st = (await api("/api/project")).state;
    assert.equal(st.hand_store.placement[`${NEW}/right_pointing`].pivot_x, 2, "manual wrist pivot stored");
    assert.equal(st.hand_store.placement[`${NEW}/right_pointing`].overlap, 1, "overlap stored");
    await page.screenshot({ path: path.join(SHOTSHR, "02_sleeve_free_wrist_controls.png") });
    await page.click("[data-testid=tab-Switches]");
    await page.waitForSelector("[data-testid=hand-decision-l]");
    await page.waitForFunction(() => /auto choice/.test(document.querySelector("[data-testid=hand-decision-l]")?.textContent ?? ""),
      null, { timeout: 120000 });
    let r1 = await rev(page);
    await page.selectOption("[data-testid=hand-action-l]", "thumbs_up");
    await page.waitForFunction((b) => !document.querySelector("[data-testid=revision]").textContent.includes(b), r1, { timeout: 180000 });
    st = (await api("/api/project")).state;
    assert.equal(st.hand_selection.actions.l, "thumbs_up", "gesture intention stored");
    pr = await post("/api/pose", { preset: "relaxed_standing", skeleton_only: true, allow_pending: true });
    assert.equal(pr.hand_selection.decisions.l.action, "thumbs_up");
    assert.equal(pr.hands.l.pose, "thumbs_up", "the engine follows the gesture intention");
    await opClick("[data-testid=sw-hand_l-autotoggle]");   // off: the current hand becomes an explicit choice
    st = (await api("/api/project")).state;
    assert.equal(st.switches.hand_l.auto, false);
    await page.screenshot({ path: path.join(SHOTSHR, "03_hand_selection_decision.png") });
    await opClick("[data-testid=sw-hand_l-auto]");          // reset to automatic
    r1 = await rev(page);
    await page.selectOption("[data-testid=hand-action-l]", "");
    await page.waitForFunction((b) => !document.querySelector("[data-testid=revision]").textContent.includes(b), r1, { timeout: 180000 });
    st = (await api("/api/project")).state;
    assert.equal(st.switches.hand_l.auto, true);
    assert.equal(st.hand_selection.actions.l, null);
    await post("/api/op", { type: "handstore.library", library: NEW, status: "archived" });   // rollback ...
    let hl = await api("/api/handstore");
    assert.equal(hl.libraries.schoolboy_hand_switches_v1.status, "active", "rollback: the old library is active again");
    await post("/api/op", { type: "handstore.library", library: NEW, status: null });          // ... and back
    hl = await api("/api/handstore");
    assert.equal(hl.libraries[NEW].status, "active");
    assert.equal(hl.libraries.schoolboy_hand_switches_v1.status, "archived");
    await page.reload();
    await page.waitForSelector("[data-testid=layers-panel]");

    // 6f. Phase 3.3 garment weights: parts overlay, bone heatmap, a brush edit (project op), explain, undo
    await page.click("[data-testid=layer-upper_arm_r]");
    await page.click("[data-testid=tab-Weights]");
    await page.waitForSelector("[data-testid=weights-panel]");
    await page.waitForFunction(() => /validation: OK/.test(document.querySelector("[data-testid=weights-panel]").textContent), null, { timeout: 180000 });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(SHOTS33, "01_weights_parts_overlay.png") });
    await page.selectOption("[data-testid=weights-view]", "influence");
    await page.selectOption("[data-testid=weights-bone]", "forearm_r");
    await page.waitForTimeout(2500);
    await page.screenshot({ path: path.join(SHOTS33, "02_weights_forearm_heatmap.png") });
    const nOps = (await api("/api/project")).state.garment_weights.ops.length;
    await post("/api/op", { type: "weights.smooth", layer: "upper_arm_r", center: [277, 474], radius: 20, strength: 0.4 });
    let gw = (await api("/api/project")).state.garment_weights.ops;
    assert.equal(gw.length, nOps + 1, "a weight edit is a project operation");
    const ex = await post("/api/garments/explain", { layer: "upper_arm_r", x: 277, y: 474 });
    assert.equal(ex.part, "elbow_r");
    assert.ok(ex.allowed_bones.includes("forearm_r") && ex.user_edits >= 1, JSON.stringify(ex));
    await post("/api/undo");
    gw = (await api("/api/project")).state.garment_weights.ops;
    assert.equal(gw.length, nOps, "undo removes the weight edit");
    const gi = await api("/api/garments");
    assert.ok(gi.available && gi.validation.ok, "garment rig and weight meshes valid");
    // step 4 dragged the right shoulder pivot: the user's pivot is kept; the left one is garment-aligned
    assert.ok(gi.pivots.some((p) => p.joint === "shoulder_r" && p.aligned === false && /editor/.test(p.reason)), JSON.stringify(gi.pivots.slice(0, 2)));
    assert.ok(gi.pivots.some((p) => p.joint === "shoulder_l" && p.aligned));
    await page.reload();
    await page.waitForSelector("[data-testid=layers-panel]");

    // 7. facial and live readiness
    await page.click("[data-testid=tab-Face]");
    await page.waitForSelector("[data-testid=cap-left_eye_blink]");
    assert.match(await page.textContent("[data-testid=cap-left_eye_blink]"), /REQUIRES_NEW_ARTWORK/);
    await page.screenshot({ path: path.join(SHOTS, "09_face.png") });
    await page.click("[data-testid=tab-Live]");
    await page.waitForSelector("[data-testid=live-panel]");
    assert.doesNotMatch(await page.textContent("[data-testid=live-panel]"), /\byes\b/, "no control claims live-ready");
    await page.screenshot({ path: path.join(SHOTS, "10_live.png") });

    // 8. reference comparison: split, diff, provenance; sign-off
    await page.click("[data-testid=tab-Compare]");
    await sleep(1500);
    await page.screenshot({ path: path.join(SHOTS, "11_compare_split.png") });
    await page.click("[data-testid=cmp-provenance]");
    await sleep(1500);
    await page.screenshot({ path: path.join(SHOTS, "12_compare_provenance.png") });
    const metrics = await api("/api/compare/metrics");
    assert.equal(metrics.fidelity.neutral_exact, true, "neutral pose still equals the reference");
    r0 = await rev(page);
    await page.click("[data-testid=signoff]");
    await waitRev(page, r0);

    // 9. undo / redo
    const cur = (await api("/api/project")).project.cursor;
    r0 = await rev(page);
    await page.click("[data-testid=undo]");
    await waitRev(page, r0);
    assert.equal((await api("/api/project")).project.cursor, cur - 1);
    r0 = await rev(page);
    await page.click("[data-testid=redo]");
    await waitRev(page, r0);
    assert.equal((await api("/api/project")).project.cursor, cur);

    // 10. save a version, restart the server, reopen: edits survive
    await api("/api/versions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label: "e2e" }) });
    const saved = (await api("/api/project")).state;
    await stopServer(server);
    server = startServer(pkg);
    await waitUp();
    await page.reload();
    await page.waitForSelector("[data-testid=layers-panel]");
    const reopened = await api("/api/project");
    assert.deepEqual(reopened.state.bones.upper_arm_r.head, saved.bones.upper_arm_r.head);
    assert.equal(reopened.state.mask_ops.length, 2);
    assert.ok(reopened.state.vectors[vid]);
    assert.equal(reopened.project.versions.length, 1);
    assert.deepEqual(reopened.state.ik.arm_r.target, saved.ik.arm_r.target, "IK target persists");
    assert.equal(reopened.state.switches.hand_r.user, "fist", "hand switch persists");
    assert.equal(reopened.state.approvals["handstore:schoolboy_hand_switches_v1/left_relaxed@v1"].status, "approved", "hand approval persists");
    assert.equal(reopened.state.hand_store.placement["schoolboy_hand_switches_v1/left_relaxed"].dx, 1, "wrist placement persists");
    assert.equal(reopened.state.hand_store.placement["schoolboy_hands_sleeveless/right_pointing"].pivot_x, 2, "manual pivot persists");

    // 11. export the Moho-ready package
    await page.click("[data-testid=tab-Export]");
    await page.click("[data-testid=export]");
    await page.waitForSelector("[data-testid=export-result]", { timeout: 120000 });
    await sleep(800);
    await page.screenshot({ path: path.join(SHOTS, "13_export.png") });
    const exp = await api("/api/gates");
    assert.equal(exp.production_ready, false, "pending approvals block production readiness");
    const txt = await page.textContent("[data-testid=export-result]");
    assert.match(txt, /Validation errors: 0/);
    // originals untouched
    assert.equal(existsSync(srcFile) ? sha(srcFile) : sha(path.join(pkg, "source/original.png")), srcSha, "source preserved");
    assert.deepEqual(errors, [], "no page errors");
  } finally {
    await browser.close();
    await stopServer(server);
    rmSync(work, { recursive: true, force: true });
  }
});
