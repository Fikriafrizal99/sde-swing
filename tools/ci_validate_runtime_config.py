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

    print(f"OK runtime_config: {provenance['validation_status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
