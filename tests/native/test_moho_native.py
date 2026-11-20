"""NATIVE Moho Pro 14 integration test - runs only on the user's Mac.

Status in cloud CI: NOT EXECUTED (skipped). The cloud sandbox has no Moho.

How to execute (see docs/phase0/install-macos-moho14.md):
  1. Install and run the generated menu script inside Moho Pro 14 on a new
     1920x1080 document (Scripts > AutoRig > AutoRig Phase 0: Build Test Rig).
     It writes <asset root>/phase0_moho_report.json.
  2. AUTORIG_MOHO_REPORT=examples/phase0_test_character/phase0_moho_report.json \
         python -m pytest -m moho_native -v
"""

import os
from pathlib import Path

import pytest

from autorig.report import load_report, verify_report

REPORT_ENV = "AUTORIG_MOHO_REPORT"

pytestmark = pytest.mark.moho_native


def _report_path() -> Path:
    value = os.environ.get(REPORT_ENV)
    if not value:
        pytest.skip(f"NOT EXECUTED: requires Moho Pro 14 on macOS. Set {REPORT_ENV} to the report "
                    "written by the AutoRig Phase 0 menu script.")
    path = Path(value)
    if not path.is_file():
        pytest.fail(f"{REPORT_ENV}={value} does not exist")
    return path


def test_moho_report_verifies_against_spec(spec):
    report = load_report(_report_path())
    result = verify_report(spec, report)
    print(result.format())
    assert result.passed, result.format()


def test_moho_embeds_lua_54():
    report = load_report(_report_path())
    assert "5.4" in str(report.get("lua_version")), report.get("lua_version")
