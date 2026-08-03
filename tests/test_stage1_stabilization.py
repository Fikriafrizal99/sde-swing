from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.data_preprocessor.stockbit_preprocessor import parse_number
from modules.decision_engine.smart_selective_v162 import finalize_after_entry_plan
from modules.runtime_config import RuntimeConfigError, load_runtime_config, write_runtime_config_audit
from swing_utils import PACKAGE_VERSION, PIPELINE_VERSION, file_sha256


def _active_config() -> dict:
    return json.loads((PROJECT_ROOT / "config" / "pipeline.json").read_text(encoding="utf-8"))


def test_runtime_config_provenance_and_audit_are_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "pipeline.json"
    source.write_text(json.dumps(_active_config(), indent=2), encoding="utf-8")

    payload, provenance = load_runtime_config(source, strict=True)
    assert provenance["validation_status"] == "VALID"
    assert provenance["config_hash"] == file_sha256(source)
    assert provenance["config_version"] == PACKAGE_VERSION
    assert provenance["pipeline_version"] == PIPELINE_VERSION
    assert provenance["override_mode"] == "NONE"
    assert provenance["legacy_overrides_detected"] == []

    audit_path = write_runtime_config_audit(tmp_path / "audit.json", provenance, payload)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["effective_values"]["candidate"] == payload["candidate"]
    assert audit["effective_values"]["decision"] == payload["decision"]
    assert audit["effective_values"]["exit"] == payload["exit"]


def test_runtime_config_rejects_source_version_mismatch(tmp_path: Path) -> None:
    payload = _active_config()
    payload["package"]["version"] = "1.6.1"
    source = tmp_path / "wrong-version.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeConfigError, match="CONFIG_VERSION_MISMATCH"):
        load_runtime_config(source, strict=True)


def test_runtime_config_rejects_legacy_override_keys(tmp_path: Path) -> None:
    payload = copy.deepcopy(_active_config())
    payload["runtime"]["legacy_config"] = {"candidate.top": 30}
    source = tmp_path / "legacy-override.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeConfigError, match="UNDOCUMENTED_LEGACY_OVERRIDE_KEYS"):
        load_runtime_config(source, strict=True)


@pytest.mark.parametrize(
    ("raw", "decimal_comma", "expected"),
    [
        ("65,12B", True, 65.12e9),
        ("(2,41B)", True, -2.41e9),
        ("2,186.59B", False, 2186.59e9),
        ("1.250,75K", True, 1_250_750),
        ("8,750", False, 8750),
        ("Rp 850,5M", True, 850.5e6),
    ],
)
def test_stockbit_number_parser_handles_mixed_locales(raw: str, decimal_comma: bool, expected: float) -> None:
    assert parse_number(raw, compact_decimal_comma=decimal_comma) == pytest.approx(expected)


def test_finalizer_promotes_only_triggered_candidate_and_preserves_trace() -> None:
    result = finalize_after_entry_plan(
        preplan_status="BUY ON TRIGGER",
        plan_status="ACCEPT",
        plan_reason="ENTRY_TRIGGERED",
        rejected_by_preplan='["ENTRY_NOT_TRIGGERED"]',
        decision_trace_preplan='["SETUP=BREAKOUT", "FINAL=72.0"]',
        major_rr=2.1,
        risk_pct=3.2,
    )
    assert result["Decision_Status_Final"] == "BUY READY"
    assert result["Execution_Status"] == "BUY CONFIRMED"
    assert result["Final_Decision_Owner"] == "DECISION_ENGINE"
    assert "ENTRY_NOT_TRIGGERED" not in json.loads(result["Rejected_By"])
    trace = json.loads(result["Decision_Trace"])
    assert "SETUP=BREAKOUT" in trace
    assert "PLAN_STATUS=ACCEPT" in trace
    assert "FINAL_STATUS=BUY READY" in trace
    assert trace[-1] == "FINAL_OWNER=DECISION_ENGINE"


def test_finalizer_does_not_promote_watch_even_when_plan_is_acceptable() -> None:
    result = finalize_after_entry_plan(
        preplan_status="WATCH",
        plan_status="ACCEPT",
        plan_reason="ENTRY_TRIGGERED",
    )
    assert result["Decision_Status_Final"] == "WATCH"
    assert result["Execution_Status"] == "MONITOR"


def test_finalizer_keeps_untriggered_candidate_out_of_active_trade() -> None:
    result = finalize_after_entry_plan(
        preplan_status="BUY ON TRIGGER",
        plan_status="CONDITIONAL",
        plan_reason="ENTRY_NOT_TRIGGERED",
    )
    assert result["Decision_Status_Final"] == "BUY ON TRIGGER"
    assert result["Execution_Status"] == "WAIT TRIGGER"
    assert "ENTRY_NOT_TRIGGERED" in json.loads(result["Rejected_By"])


def test_serious_entry_plan_failure_is_auditable_hard_rejection() -> None:
    result = finalize_after_entry_plan(
        preplan_status="BUY ON TRIGGER",
        plan_status="REJECT",
        plan_reason="RISK_REWARD_BELOW_MINIMUM",
    )
    assert result["Decision_Status_Final"] == "AVOID"
    assert result["Final_Decision_Owner"] == "DECISION_ENGINE"
    assert "RISK_REWARD_BELOW_MINIMUM" in json.loads(result["Rejected_By"])


def test_telegram_counts_use_final_entry_plan_status() -> None:
    import pandas as pd
    from modules.telegram.professional_ui import public_counts

    decisions = pd.DataFrame([
        {"Symbol": "AAAA", "Decision_Status": "BUY ON TRIGGER", "Decision_V3": "BUY CANDIDATE"},
        {"Symbol": "BBBB", "Decision_Status": "BUY ON TRIGGER", "Decision_V3": "BUY CANDIDATE"},
        {"Symbol": "CCCC", "Decision_Status": "WATCH", "Decision_V3": "WATCH"},
    ])
    plans = pd.DataFrame([
        {"Symbol": "AAAA", "Decision_Status_Final": "BUY READY", "Plan_Status": "ACCEPT"},
        {"Symbol": "BBBB", "Decision_Status_Final": "BUY ON TRIGGER", "Plan_Status": "CONDITIONAL"},
    ])
    assert public_counts(decisions, plans) == {
        "BUY CONFIRMED": 1,
        "BUY CANDIDATE": 1,
        "WATCH HIGH": 0,
        "WATCH": 1,
        "AVOID": 0,
    }
