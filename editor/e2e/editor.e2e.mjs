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
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
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
const PORT = 8790 + Math.floor(Math.random() * 100);
const BASE = `http://127.0.0.1:${PORT}`;
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
  cpSync(PKG, pkg, { recursive: true, filter: (s) => !s.includes(`${path.sep}editor`) && !/natural_pose[\\/](poses|legacy|closeups)/.test(s) });
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
