# Phase 4.1: performance

**Native measurements: NOT EXECUTED.** No Moho run of the full-body rig exists, so there are no M1 figures. The cloud
figures below describe AutoRig's own tooling on a 4-core x86_64 cloud container. They are not Moho or Apple M1
measurements and must not be read as such.

## 1. Cloud (AutoRig tooling, not Moho)

| step | schoolboy | tough student |
|---|---|---|
| derive the native skeleton (editor skeleton + aligned pivots) | 2.3 s | 1.9 s |
| build the package without simulated frames | 4.8 s | 4.9 s |
| build the package with the 18 simulated frames | 30.6 s | 29.4 s |
| art written (25 PNG layers) | 0.80 MB | 0.87 MB |
| build / verify / IK script in the strict mock | 0.005 / 0.003 / 0.001 s | same |

The simulated frames dominate the build time. `render_expected=False` skips them for repeated builds. Artwork quality
was not reduced to save time.

## 2. What the Mac run records

| measure | how | status |
|---|---|---|
| package build time on the M1 | the harness (`package_build_seconds`) | NOT EXECUTED |
| native rig build (bones, image import, binding) | the build script's alert; the harness logs when the report appears. The Phase 0 template has no per-step timer, and it is not modified | NOT EXECUTED |
| verify read-back time | `timings_s.readback_cpu` / `total_cpu` in the verify report (Lua `os.clock`) | NOT EXECUTED |
| reopen duration | `reopen_seconds_until_answer` (an upper bound: it includes your answer) | NOT EXECUTED |
| Moho memory | `moho_rss_kb_after_build`, `moho_rss_kb_after_reopen` (`ps` on the Moho process) | NOT EXECUTED |
| rendering time | `render_seconds_renders_1_110` (command-line render), and the IK frames | NOT EXECUTED |
| viewport responsiveness, IK drag latency | your observation during the drag tests (no instrumented API) | NOT EXECUTED |

All of these go into `observations.json` and the reports of the run folder, and from there into this report.
