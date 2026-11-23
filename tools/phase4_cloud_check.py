"""Phase 4.0 cloud check: what the cloud CAN show without Moho.

For each character package: build the proof package (reference build, no Mac paths),
run the build script and the verify-after-reopen script against the strict mock Moho
API (the "reopen" is simulated: the same mock document), render the expected frames
with the Python simulation, and evaluate HARD GATE 4.0 on that evidence. The gate must
come out BLOCKED with every item MOCK_ONLY or NOT_EXECUTED.

Output goes to docs/phase4/cloud_checks/<character>/ and is labelled as mock/simulation.
It is NOT Moho evidence; native runs go to docs/phase4/evidence/native_runs/.

    .venv/bin/python tools/phase4_cloud_check.py
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

from autorig.native.gate import evaluate_gate, format_gate  # noqa: E402
from autorig.native.poc import (BUILD_REPORT, BUILD_SCRIPT, FRAME_MOVED, FRAME_REOPEN, FRAME_REST,  # noqa: E402
                                VERIFY_REPORT, VERIFY_SCRIPT, build_poc)

PACKAGES = {
    "schoolboy_tpose": ROOT / "projects/schoolboy_tpose/character_package",
    "tough_student_test": ROOT / "projects/tough_student_test/character_package",
}
OUT = ROOT / "docs/phase4/cloud_checks"


def run_mock(poc: Path, man: dict) -> list[dict]:
    from lupa.lua54 import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    mock = lua.execute((ROOT / "tests/lua/mock_moho.lua").read_text(encoding="utf-8"))
    env = mock.new_env(lua.table_from({"width": 1920, "height": 1080}, recursive=True))
    for key, name in (("build", BUILD_SCRIPT), ("verify", VERIFY_SCRIPT)):
        lua.execute((poc / man["scripts"][key]).read_text(encoding="utf-8"))
        script = lua.globals()[name]
        script.Run(script, env.moho)
    return [dict(sorted(dict(a).items())) for a in env.alerts.values()]  # Lua key order is not stable


def check(name: str, pkg: Path) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        poc = Path(tmp) / "poc"
        man = build_poc(pkg, poc, asset_root=str(poc.resolve()))
        alerts = run_mock(poc, man)
        ev = Path(tmp) / "evidence"
        (ev / "renders").mkdir(parents=True)
        for f in (BUILD_REPORT, VERIFY_REPORT):
            shutil.copy(poc / f, ev / f)
        for key, frame in (("rest", FRAME_REST), ("moved", FRAME_MOVED), ("reopen_move", FRAME_REOPEN)):
            im = Image.open(poc / man["expected"][key]).convert("RGBA")
            flat = Image.new("RGBA", im.size, (236, 236, 236, 255))
            flat.alpha_composite(im)
            flat.convert("RGB").save(ev / "renders" / f"frame_{frame:04d}.png")
        res = evaluate_gate(poc, ev, renders_simulated=True)
        dest = OUT / name
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        shutil.copy(poc / "expected/expected_contact_sheet.png", dest / "SIMULATION_expected_frames.png")
        shutil.copy(poc / "rig_spec.json", dest / "rig_spec.json")
        m = json.loads((poc / "native_manifest.json").read_text())
        m["asset_root"] = "<baked on the Mac by tools/macos/phase4_native_test.sh>"
        (dest / "native_manifest.json").write_text(json.dumps(m, indent=2) + "\n")
        def scrub(text: str) -> str:  # temporary paths -> placeholders, so reruns are reproducible
            return text.replace(str(poc.resolve()), "<poc>").replace(str(ev), "<evidence>").replace(tmp, "<tmp>")

        for f in (BUILD_REPORT, VERIFY_REPORT):
            r = json.loads((poc / f).read_text())
            r["_label"] = "MOCK: written by the strict mock Moho API in the cloud, not by Moho"
            (dest / f"MOCK_{f}").write_text(scrub(json.dumps(r, indent=2)) + "\n")
        res["_label"] = ("cloud check: mock reports + simulated renders; HARD GATE 4.0 cannot pass on this "
                         "evidence by design")
        res["poc"]["dir"] = "<temporary>"
        res["mock_alerts"] = alerts
        (dest / "gate_4_0_mock.json").write_text(scrub(json.dumps(res, indent=2, default=str)) + "\n")
        print(format_gate(res))
        return {"character": name, "verdict": res["verdict"],
                "items": {i["id"]: i["status"] for i in res["items"]},
                "placement": m["placement"], "split_fidelity": m["split_fidelity"]}


def main() -> int:
    summary = [check(name, pkg) for name, pkg in PACKAGES.items()]
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    ok = all(s["verdict"].startswith("BLOCKED") for s in summary)
    print("cloud check:", "as expected (BLOCKED on mock evidence)" if ok else "UNEXPECTED verdict")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
