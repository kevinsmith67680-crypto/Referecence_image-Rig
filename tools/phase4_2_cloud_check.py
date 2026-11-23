"""Phase 4.2 cloud check: what the cloud CAN show without Moho, for both characters.

Per character package:
1. build the hybrid native package (``autorig native-hybrid``), default and review;
2. run the build and verify-after-reopen scripts against the strict mock Moho API (the "reopen" is the same mock
   document);
3. stand in the simulated expected frames for the renders;
4. evaluate the Phase 4.2 gates.

They must all be MOCK_ONLY, MEASURED or NOT_EXECUTED. Output goes to docs/phase4_2/cloud_checks/<character>/,
labelled MOCK/SIMULATION. It is NOT Moho evidence; native runs go to docs/phase4/evidence/native_runs_4_2/.

    .venv/bin/python tools/phase4_2_cloud_check.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autorig.native.gate42 import evaluate_gate42, format_gate42  # noqa: E402
from autorig.native.hybrid_build import (BUILD_REPORT, BUILD_SCRIPT, VERIFY_REPORT, VERIFY_SCRIPT,  # noqa: E402
                                         build_hybrid)

PACKAGES = {
    "schoolboy_tpose": ROOT / "projects/schoolboy_tpose/character_package",
    "tough_student_test": ROOT / "projects/tough_student_test/character_package",
}
OUT = ROOT / "docs/phase4_2/cloud_checks"
AUDIT = ROOT / "docs/phase4_2/audit"


def run_mock(pkg_out: Path, man: dict) -> list[dict]:
    from lupa.lua54 import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    mock = lua.execute((ROOT / "tests/lua/mock_moho.lua").read_text(encoding="utf-8"))
    w, h = man["moho_canvas"]
    env = mock.new_env(lua.table_from({"width": w, "height": h}, recursive=True))
    for key, name in (("build", BUILD_SCRIPT), ("verify", VERIFY_SCRIPT)):
        lua.execute((pkg_out / man["scripts"][key]).read_text(encoding="utf-8"))
        script = lua.globals()[name]
        script.Run(script, env.moho)
    return [dict(sorted(dict(a).items())) for a in env.alerts.values()]


def check(name: str, pkg: Path) -> dict:
    audit = AUDIT / name / "joint_audit.json"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "pkg"
        man = build_hybrid(pkg, out, asset_root=str(out.resolve()), audit=audit if audit.is_file() else None)
        alerts = run_mock(out, man)
        ev = Path(tmp) / "evidence"
        (ev / "renders").mkdir(parents=True)
        for f in (BUILD_REPORT, VERIFY_REPORT):
            shutil.copy(out / f, ev / f)
        for p in json.loads((out / "validation_plan.json").read_text())["poses"]:
            im = Image.open(out / man["expected"][str(p["frame"])]).convert("RGBA")
            flat = Image.new("RGBA", im.size, (236, 236, 236, 255))
            flat.alpha_composite(im)
            flat.convert("RGB").save(ev / "renders" / f"frame_{p['frame']:04d}.png")
        res = evaluate_gate42(out, ev, character_package=pkg, renders_simulated=True)
        review = Path(tmp) / "review"
        rman = build_hybrid(pkg, review, review=True, audit=audit if audit.is_file() else None)
        dest = OUT / name
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)

        def scrub(text: str) -> str:
            return (text.replace(str(out.resolve()), "<package>").replace(str(review.resolve()), "<review>")
                    .replace(str(ev), "<evidence>").replace(tmp, "<tmp>"))

        for src, tag in ((out, ""), (review, "review_")):
            for key in ("sheet", "planned_sheet"):
                m = man if src is out else rman
                Image.open(src / m["expected"][key]).convert("RGB").save(
                    dest / f"SIMULATION_{tag}{Path(m['expected'][key]).stem.replace('SIMULATION_', '')}.jpg",
                    quality=85, optimize=True)
        for f in ("binding_plan.json", "smartbone_plan.json", "artwork_requests.json", "manual_tests.json",
                  "validation_plan.json", "rig_spec.json"):
            shutil.copy(out / f, dest / f)
        shutil.copy(review / "binding_plan.json", dest / "review_binding_plan.json")
        for m, fname in ((man, "native_hybrid_manifest.json"), (rman, "review_native_hybrid_manifest.json")):
            mm = dict(m)
            mm.pop("file_hashes", None)
            (dest / fname).write_text(scrub(json.dumps(mm, indent=2)) + "\n")
        for f in (BUILD_REPORT, VERIFY_REPORT):
            r = json.loads((out / f).read_text())
            r["_label"] = "MOCK: written by the strict mock Moho API in the cloud, not by Moho"
            if "timings_s" in r:  # CPU times vary from run to run; keep the committed output reproducible
                r["timings_s"] = {k: "<varies per run>" for k in r["timings_s"]}
            (dest / f"MOCK_{f}").write_text(scrub(json.dumps(r, indent=2)) + "\n")
        res["_label"] = "cloud check: mock reports + simulated renders; the Phase 4.2 gates cannot pass on this evidence"
        res["mock_alerts"] = alerts
        (dest / "gate_4_2_mock.json").write_text(scrub(json.dumps(res, indent=2, default=str)) + "\n")
        print(format_gate42(res))
        return {"character": name, "verdict": res["verdict"], "gates": {g["id"]: g["status"] for g in res["gates"]},
                "moho_canvas": man["moho_canvas"], "layers": man["layers"]["by_status"],
                "review_layers": rman["layers"]["by_status"], "partition_exact": man["layers"]["partition_exact"],
                "manual_switch_assets": man["manual_switch_assets"]}


def main() -> int:
    summary = [check(name, pkg) for name, pkg in PACKAGES.items()]
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    ok = all(s["verdict"].startswith("BLOCKED") and all(v in ("MOCK_ONLY", "NOT_EXECUTED", "MEASURED")
                                                        for v in s["gates"].values()) for s in summary)
    print("cloud check:", "as expected (no gate passes on mock evidence)" if ok else "UNEXPECTED result")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
