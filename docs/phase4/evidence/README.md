# Phase 4 evidence: real application evidence only

This folder holds evidence produced by the **real Moho application** on the user's Mac. Nothing in it is produced,
edited or simulated in the cloud.

| item | status |
|---|---|
| `native_runs/20261123T105258Z_schoolboy_tpose_l/` | run 1: stopped waiting for the build report. Its `AutoRig_P4_Native.moho` was saved here *during run 2* (see `docs/phase4_completion_report.md` §2) |
| `native_runs/20261123T105654Z_schoolboy_tpose_l/` | run 2: native build report (Moho 14.5.1). Stopped at Save As because the file went into run 1's folder |
| next run | a fresh full run of `tools/macos/phase4_native_test.sh` creates `native_runs/<UTC time>_<character>_<side>/` |

A native run folder contains:

| file | written by |
|---|---|
| `environment.json` | the harness: macOS version, architecture, Moho bundle path, version and build (from `Info.plist`) |
| `phase4_native_build_report.json` | the build menu script, inside Moho |
| `AutoRig_P4_Native.moho` | Moho (File > Save As, then Save after the verify script) |
| `phase4_native_verify_report.json` | the verify menu script, inside Moho, on the reopened document |
| `renders/frame_0001.png`, `frame_0012.png`, `frame_0018.png` | Moho (command-line render or File > Export Animation); `renders/cli_render.log` records the command-line attempts |
| `observations.json` | the user's answers to the harness prompts (what they saw in Moho) |
| `screenshots/`, `lua_console.txt` | optional, made by the user |
| `harness.log`, `gate_4_0.json` | the harness and `autorig native-verify` |
| `rig_spec.json`, `native_manifest.json` | copies of the proof package's spec and provenance |

Mock and simulation results live elsewhere, labelled as such: `docs/phase4/cloud_checks/`.
