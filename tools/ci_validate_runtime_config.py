"""CI: validate runtime config invariants.

Checks that pipeline.json passes strict validation and that auto_entry_enabled
is False.  Exits non-zero on any violation.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.runtime_config import load_runtime_config, RuntimeConfigError  # noqa: E402
from swing_utils import DISPLAY_VERSION, PACKAGE_VERSION, PIPELINE_VERSION  # noqa: E402


EXPECTED_VERSION = "1.7.1"


def main() -> int:
    config_path = ROOT / "config" / "pipeline.json"
    if not config_path.exists():
        print(f"SKIP: {config_path} not found (no pipeline config to validate)")
        return 0
    try:
        payload, provenance = load_runtime_config(config_path, strict=True)
    except RuntimeConfigError as exc:
        print(f"FAIL runtime_config: {exc}")
        return 1

    # Hard invariant: auto-entry must never be enabled on this branch.
    decision = payload.get("decision", {})
    calibration = decision.get("calibration", {}) if isinstance(decision, dict) else {}
    if calibration.get("auto_entry_enabled", False):
        print("FAIL: auto_entry_enabled is True — must remain False")
        return 1

    version_path = ROOT / "VERSION"
    version_file = version_path.read_text(encoding="utf-8").strip() if version_path.exists() else ""
    if version_file != EXPECTED_VERSION:
        print(f"FAIL: VERSION must be {EXPECTED_VERSION}")
        return 1

    package = payload.get("package", {})
    if str(package.get("version", "")) != EXPECTED_VERSION or str(package.get("pipeline_version", "")) != EXPECTED_VERSION:
        print(f"FAIL: package and pipeline version must be {EXPECTED_VERSION}")
        return 1
    if PACKAGE_VERSION != EXPECTED_VERSION or PIPELINE_VERSION != EXPECTED_VERSION:
        print(f"FAIL: runtime version constants are not {EXPECTED_VERSION}")
        return 1
    if DISPLAY_VERSION != f"SDE Swing V{EXPECTED_VERSION}":
        print(f"FAIL: display version must be SDE Swing V{EXPECTED_VERSION}")
        return 1
    if str(payload.get("config_version", PACKAGE_VERSION)) != EXPECTED_VERSION:
        print(f"FAIL: config_version must be {EXPECTED_VERSION}")
        return 1

    print(f"OK runtime_config: {provenance['validation_status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
