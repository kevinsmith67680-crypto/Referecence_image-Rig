"""Phase 4.1 cloud check: what the cloud CAN show without Moho, for both characters.

Per character package:
1. build the full-body package (``autorig native-fullbody``);
2. run the build, verify-after-reopen and IK/constraints scripts against the strict mock Moho API (the "reopen"
   is the same mock document);
3. stand in the simulated expected frames for the renders;
4. evaluate the Phase 4.1 gates.

They must all be MOCK_ONLY or NOT_EXECUTED. Output goes to docs/phase4_1/cloud_checks/<character>/, labelled
MOCK/SIMULATION. It is NOT Moho evidence; native runs go to docs/phase4/evidence/native_runs_4_1/.

    .venv/bin/python tools/phase4_1_cloud_check.py
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

from autorig.native.fullbody import (BUILD_REPORT, BUILD_SCRIPT, IK_REPORT, IK_SCRIPT, VERIFY_REPORT,  # noqa: E402
                                     VERIFY_SCRIPT, build_fullbody)
from autorig.native.gate41 import evaluate_gate41, format_gate41  # noqa: E402

PACKAGES = {
    "schoolboy_tpose": ROOT / "projects/schoolboy_tpose/character_package",
    "tough_student_test": ROOT / "projects/tough_student_test/character_package",
}
OUT = ROOT / "docs/phase4_1/cloud_checks"


def run_mock(pkg_out: Path, man: dict) -> list[dict]:
    from lupa.lua54 import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    mock = lua.execute((ROOT / "tests/lua/mock_moho.lua").read_text(encoding="utf-8"))
    w, h = man["moho_canvas"]
    env = mock.new_env(lua.table_from({"width": w, "height": h}, recursive=True))
    for key, name in (("build", BUILD_SCRIPT), ("verify", VERIFY_SCRIPT), ("ik", IK_SCRIPT)):
        lua.execute((pkg_out / man["scripts"][key]).read_text(encoding="utf-8"))
        script = lua.globals()[name]
        script.Run(script, env.moho)
    return [dict(sorted(dict(a).items())) for a in env.alerts.values()]


def simulated_renders(pkg_out: Path, man: dict, ev: Path) -> None:
    for sub, keys in (("renders", [k for k in man["expected"] if k.isdigit() and int(k) <= 110]),
                      ("renders_ik", [k for k in man["expected"] if k.isdigit() and int(k) > 110] + ["160_clamped"])):
        (ev / sub).mkdir(parents=True, exist_ok=True)
        for k in keys:
            im = Image.open(pkg_out / man["expected"][k]).convert("RGBA")
            flat = Image.new("RGBA", im.size, (236, 236, 236, 255))
            flat.alpha_composite(im)
            frame = int(k.split("_")[0])
            flat.convert("RGB").save(ev / sub / f"frame_{frame:04d}.png")


def check(name: str, pkg: Path) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "pkg"
        man = build_fullbody(pkg, out, asset_root=str(out.resolve()))
        alerts = run_mock(out, man)
        ev = Path(tmp) / "evidence"
        ev.mkdir()
        for f in (BUILD_REPORT, VERIFY_REPORT, IK_REPORT):
            shutil.copy(out / f, ev / f)
        simulated_renders(out, man, ev)
        res = evaluate_gate41(out, ev, character_package=pkg, renders_simulated=True)
        dest = OUT / name
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)

        def scrub(text: str) -> str:
            return text.replace(str(out.resolve()), "<package>").replace(str(ev), "<evidence>").replace(tmp, "<tmp>")

        sheet = Image.open(out / man["expected"]["sheet"]).convert("RGB")
        sheet.save(dest / "SIMULATION_pose_sheet.jpg", quality=85, optimize=True)
        for f in ("native_skeleton.json", "binding_plan.json", "validation_plan.json", "rig_spec.json"):
            shutil.copy(out / f, dest / f)
        m = dict(man)
        m.pop("file_hashes", None)
        (dest / "native_fullbody_manifest.json").write_text(scrub(json.dumps(m, indent=2)) + "\n")
        for f in (BUILD_REPORT, VERIFY_REPORT, IK_REPORT):
            r = json.loads((out / f).read_text())
            r["_label"] = "MOCK: written by the strict mock Moho API in the cloud, not by Moho"
            (dest / f"MOCK_{f}").write_text(scrub(json.dumps(r, indent=2)) + "\n")
        res["_label"] = "cloud check: mock reports + simulated renders; the Phase 4.1 gates cannot pass on this evidence"
        res["mock_alerts"] = alerts
        (dest / "gate_4_1_mock.json").write_text(scrub(json.dumps(res, indent=2, default=str)) + "\n")
        print(format_gate41(res))
        return {"character": name, "verdict": res["verdict"], "gates": {g["id"]: g["status"] for g in res["gates"]},
                "moho_canvas": man["moho_canvas"], "placement": man["placement"], "bones": man["bones"],
                "binding_counts": man["binding_counts"], "layers": man["layers"]["count"]}


def main() -> int:
    summary = [check(name, pkg) for name, pkg in PACKAGES.items()]
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    ok = all(s["verdict"].startswith("BLOCKED") and all(v in ("MOCK_ONLY", "NOT_EXECUTED") for v in s["gates"].values())
             for s in summary)
    print("cloud check:", "as expected (no gate passes on mock evidence)" if ok else "UNEXPECTED result")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
