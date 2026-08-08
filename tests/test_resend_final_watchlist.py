from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from tools.resend_final_watchlist import _csv_last, find_existing_run_manifest


def test_find_existing_run_manifest_selects_matching_trade_date(tmp_path: Path) -> None:
    old = tmp_path / "SWING_RUN_MANIFEST_OLD.json"
    target = tmp_path / "SWING_RUN_MANIFEST_TARGET.json"
    old.write_text(json.dumps({"Technical_Date": "2026-08-06", "Pipeline_Status": "SUCCESS"}), encoding="utf-8")
    target.write_text(json.dumps({"Technical_Date": "2026-08-07", "Pipeline_Status": "SUCCESS", "Run_ID": "TARGET"}), encoding="utf-8")

    path, payload = find_existing_run_manifest(tmp_path, "2026-08-07")

    assert path == target
    assert payload["Run_ID"] == "TARGET"


def test_find_existing_run_manifest_rejects_failed_run(tmp_path: Path) -> None:
    failed = tmp_path / "SWING_RUN_MANIFEST_FAILED.json"
    failed.write_text(json.dumps({"Technical_Date": "2026-08-07", "Pipeline_Status": "FAILED"}), encoding="utf-8")

    path, payload = find_existing_run_manifest(tmp_path, "2026-08-07")

    assert path is None
    assert payload == {}


def test_csv_attachment_is_always_last() -> None:
    csv_payload = SimpleNamespace(attachment_path=Path("final_watchlist.csv"))
    chart_payload = SimpleNamespace(attachment_path=Path("ANTM_setup.png"))
    text_payload = SimpleNamespace(attachment_path=None)

    ordered = _csv_last([csv_payload, chart_payload, text_payload])

    assert ordered == [chart_payload, text_payload, csv_payload]
