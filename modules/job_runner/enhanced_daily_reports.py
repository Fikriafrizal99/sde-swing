from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from modules.ai_interpretation import GeminiInterpreter
from modules.telegram.daily_report_ui import (
    format_broker_summary,
    format_final_watchlist_summary,
    format_market_outlook,
    format_post_market,
    format_watchlist_detail,
)
from modules.telegram.final_watchlist_chart import (
    compact_final_watchlist_caption,
    generate_final_watchlist_chart,
)


@dataclass(frozen=True)
class DailyReportArtifact:
    report_type: str
    text: str
    topic: str = "report"
    symbol: str = ""
    attachment_path: Path | None = None
    caption: str = ""
    # Lineage metadata is carried with the artifact so the Telegram layer can
    # remain a pure formatter and the runtime can write an audit event without
    # reconstructing the source graph.
    input_paths: tuple[str, ...] = ()
    source_of_truth: tuple[str, ...] = ()
    row_count: int | None = None
    validation_details: dict[str, Any] | None = None


FINAL_WATCHLIST_COLUMNS = [
    "trade_date", "rank", "symbol", "decision", "execution_state",
    "confidence", "setup", "trend", "technical_quality", "technical_score",
    "technical_state", "entry_readiness", "momentum_status", "rsi",
    "volume_description", "volume_ratio_ma20", "last_price",
    "entry_low", "entry_high", "entry_distance_pct", "stop_loss",
    "target_1", "target_2", "risk_reward", "broker_status",
    "broker_direction", "broker_confidence", "broker_score", "broker_state",
    "broker_net_flow", "broker_buy_ratio", "broker_sell_ratio",
    "broker_alignment", "top_buyers", "top_sellers", "avg_buyer_price",
    "avg_seller_price", "distance_to_buyer_avg_pct", "broker_raw_coverage",
    "buyer_concentration", "seller_concentration", "broker_pattern", "avg_accdist",
    "bandar_buy_cost", "distance_to_buy_cost", "analysis_date", "active_stop_loss",
    "technical_status", "phase", "support", "resistance", "fib_status",
    "swing_high", "swing_low", "engine_final_reason",
    "broker_period_type", "broker_period_start", "broker_period_end",
    "broker_trading_days", "broker_session_dates", "broker_snapshot_id",
    "broker_period_source", "broker_coverage", "broker_period_coverage",
    "broker_coverage_text", "broker_coverage_status", "broker_freshness_status",
    "primary_raw_status", "today_pulse_available", "today_pulse_date",
    "today_pulse_snapshot_id", "today_pulse_source", "today_pulse_status", "today_pulse_net_flow",
    "today_pulse_broker_state", "today_pulse_direction", "today_pulse_avg_accdist",
    "today_pulse_buyer_concentration", "today_pulse_seller_concentration",
    "today_pulse_top_buyers", "today_pulse_top_sellers", "today_raw_status",
    "sector_state", "market_regime", "trigger_description",
    "waiting_triggers", "main_reason_technical", "main_reason_broker",
    "main_reason_entry", "main_reason", "risk_items", "main_risk",
    "invalidation", "execution_note", "data_status", "data_conflict",
    "exchange_status", "exchange_veto", "risk_flags", "exchange_history_candles",
    "source", "yahoo_status", "zapi_status", "reconciliation_status",
    "zapi_freshness_days", "foreign_buy", "foreign_sell", "foreign_net",
    "provider", "source_mode", "coverage", "generated_at",
]

BROKER_SUMMARY_COLUMNS = [
    "trade_date", "symbol", "broker_state", "broker_score", "net_flow",
    "buy_ratio", "sell_ratio", "top_buyer", "top_seller", "broker_1d",
    "broker_3d", "broker_5d", "data_status", "source",
    "broker_period_type", "broker_period_start", "broker_period_end",
    "broker_trading_days", "broker_session_dates", "broker_snapshot_id",
    "broker_period_source", "broker_coverage", "broker_session_coverage",
    "broker_period_coverage", "broker_missing_sessions", "broker_period_complete",
    "broker_coverage_text", "broker_coverage_status", "broker_freshness_status",
]

# Telegram detail cards are intentionally action-oriented. WATCH/AVOID remain
# available in summary counts and the complete CSV for audit, but they do not
# consume detail-card/chart delivery slots.
FINAL_WATCHLIST_DETAIL_DECISIONS = {"BUY ON TRIGGER", "BUY CANDIDATE"}


def _norm(value: Any) -> str:
    return "".join(char.lower() if char.isalnum() else "_" for char in str(value or "")).strip("_")


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _value(row: Mapping[str, Any], *aliases: str, default: Any = "") -> Any:
    lookup = {_norm(key): value for key, value in row.items()}
    for alias in aliases:
        found = lookup.get(_norm(alias))
        if found is not None and str(found).strip().lower() not in {"", "nan", "none", "null"}:
            return found
    return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        text = str(value).strip()
        if ":" in text:
            text = text.split(":")[-1].strip()
        if "," in text and "." not in text:
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
        return float(text)
    except Exception:
        return default


def _split_items(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [
        item.strip(" •-\t")
        for item in re.split(r"\s*(?:\r?\n|[;|])\s*", text)
        if item.strip(" •-\t")
    ]


class EnhancedDailyReportBuilder:
    """Build Telegram UI and CSV artifacts from engine-owned data.

    Input values remain engine facts. Presentation enrichment reads only
    already-produced report artifacts and never changes scoring, decisions,
    thresholds, source ownership, or trade-plan calculations.
    """

    def __init__(
        self,
        output_root: str | Path = "data/output",
        interpreter: GeminiInterpreter | None = None,
        max_watchlist_messages: int = 5,
    ) -> None:
        self.output_root = Path(output_root)
        self.interpreter = interpreter or GeminiInterpreter()
        # 0 means unlimited detail cards after the actionable-status filter.
        self.max_watchlist_messages = max(0, int(max_watchlist_messages))

    def build_all(self, bundle: dict[str, Any]) -> list[DailyReportArtifact]:
        artifacts: list[DailyReportArtifact] = []
        if bundle.get("market_outlook"):
            artifacts.append(self.build_market_outlook(dict(bundle["market_outlook"])))
        if bundle.get("post_market"):
            artifacts.append(self.build_post_market(dict(bundle["post_market"])))
        if bundle.get("broker_summary"):
            summary, csv_artifact = self.build_broker_summary(dict(bundle["broker_summary"]))
            artifacts.extend([summary, csv_artifact])
        if bundle.get("final_watchlist"):
            artifacts.extend(self.build_final_watchlist(dict(bundle["final_watchlist"])))
        return artifacts

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    @staticmethod
    def _read_json_optional(path: Path) -> dict[str, Any]:
        if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _read_csv_optional(path: Path) -> list[dict[str, Any]]:
        if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
            return []
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                return [dict(row) for row in csv.DictReader(handle)]
        except Exception:
            return []

    @staticmethod
    def _symbol(value: Any) -> str:
        return str(value or "").strip().upper().replace(".JK", "")

    def _symbol_map(self, path: Path) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for row in self._read_csv_optional(path):
            symbol = self._symbol(_value(row, "Symbol", "EMITEN", "Ticker", "symbol"))
            if symbol:
                result[symbol] = row
        return result

    def _directory_symbol_map(self, path: Path) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        if not path.exists() or not path.is_dir():
            return result
        for csv_path in sorted(path.glob("*.csv")):
            for symbol, row in self._symbol_map(csv_path).items():
                current = result.setdefault(symbol, {})
                for key, value in row.items():
                    if not _present(current.get(key)) and _present(value):
                        current[key] = value
        return result

    def _market_context(self, trade_date: str) -> dict[str, Any]:
        if not trade_date:
            return {}
        global_path = self.output_root / "global_market" / trade_date / "global_market_snapshot.json"
        regime_path = self.output_root / "market_regime" / trade_date / "market_outlook_regime.json"
        global_snapshot = self._read_json_optional(global_path)
        regime = self._read_json_optional(regime_path)

        rotation: dict[str, Any] = {}
        if isinstance(regime.get("sector_rotation"), dict):
            rotation = dict(regime["sector_rotation"])
        rotation_path = regime.get("sector_rotation_path")
        if rotation_path and not rotation:
            candidate = Path(str(rotation_path))
            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate
            payload = self._read_json_optional(candidate)
            if isinstance(payload.get("sector_rotation"), dict):
                rotation = dict(payload["sector_rotation"])
            elif payload:
                rotation = payload

        sentiment = global_snapshot.get("global_sentiment")
        sentiment = dict(sentiment) if isinstance(sentiment, dict) else {}
        coverage = global_snapshot.get("coverage_ratio")
        if coverage not in (None, ""):
            try:
                coverage = float(coverage)
                if 0 <= coverage <= 1:
                    coverage *= 100.0
            except Exception:
                pass

        market_dates = [
            str(item.get("market_date"))
            for item in global_snapshot.get("instruments", []) or []
            if isinstance(item, dict) and item.get("market_date")
        ]
        return {
            "market_regime": regime.get("market_regime") or regime.get("regime"),
            "execution_mode": regime.get("execution_mode"),
            "ihsg_change": regime.get("ihsg_change_pct", regime.get("ihsg_change")),
            "ihsg_trend": regime.get("trend", regime.get("ihsg_trend")),
            "ihsg_momentum": regime.get("momentum", regime.get("ihsg_momentum")),
            "breadth": regime.get("breadth", regime.get("market_breadth")),
            "ihsg_reason": regime.get("reason"),
            "confidence_pct": regime.get("confidence_pct"),
            "ihsg_data_date": regime.get("data_date"),
            "global_instruments": global_snapshot.get("instruments", []),
            "global_sentiment": sentiment,
            "global_tone": sentiment.get("sentiment_state"),
            "global_coverage": coverage,
            "global_data_date": max(market_dates) if market_dates else global_snapshot.get("trade_date"),
            "global_market_status": (
                "VALID" if global_snapshot and not global_snapshot.get("errors")
                else "VALID_WITH_WARNING" if global_snapshot
                else "NOT_ATTACHED"
            ),
            "snapshot_id": global_snapshot.get("snapshot_id"),
            "snapshot_created_at": global_snapshot.get("created_at"),
            "leading": rotation.get("leading", []),
            "rotating_in": rotation.get("improving", rotation.get("rotating_in", [])),
            "weakening": rotation.get("weakening", []),
            "rotating_out": rotation.get("lagging", rotation.get("rotating_out", [])),
            "lagging": rotation.get("lagging", rotation.get("rotating_out", [])),
        }

    @staticmethod
    def _merge_missing(target: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        merged = dict(target)
        for key, value in context.items():
            if not _present(merged.get(key)) and _present(value):
                merged[key] = value
        return merged

    @staticmethod
    def _nested_find(payload: Any, aliases: Iterable[str]) -> Any:
        wanted = {_norm(alias) for alias in aliases}
        if isinstance(payload, Mapping):
            for key, value in payload.items():
                if _norm(key) in wanted and _present(value):
                    return value
            for value in payload.values():
                found = EnhancedDailyReportBuilder._nested_find(value, aliases)
                if _present(found):
                    return found
        elif isinstance(payload, list):
            for value in payload:
                found = EnhancedDailyReportBuilder._nested_find(value, aliases)
                if _present(found):
                    return found
        return ""

    def _resolve_path(self, raw: Any) -> Path | None:
        if raw in (None, ""):
            return None
        path = Path(str(raw))
        if not path.is_absolute():
            path = Path.cwd() / path
        return path

    def _latest_manifest(self, trade_date: str) -> dict[str, Any]:
        manifest_root = self.output_root / "manifests"
        if not manifest_root.exists():
            return {}
        candidates = sorted(
            manifest_root.glob("SWING_RUN_MANIFEST_*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        fallback: dict[str, Any] = {}
        for path in candidates:
            payload = self._read_json_optional(path)
            if not payload:
                continue
            payload = {**payload, "_manifest_path": str(path)}
            if not fallback:
                fallback = payload
            payload_date = self._nested_find(payload, ("Technical_Date", "trade_date", "Trade_Date"))
            if not trade_date or str(payload_date) == trade_date:
                return payload
        return fallback

    @staticmethod
    def _dominant_filters(*payloads: Mapping[str, Any]) -> list[dict[str, Any]]:
        aliases = (
            "dominant_filters", "filter_counts", "rejection_counts",
            "rejection_reasons", "filter_reasons", "candidate_rejections",
        )
        raw: Any = ""
        for payload in payloads:
            raw = EnhancedDailyReportBuilder._nested_find(payload, aliases)
            if _present(raw):
                break
        items: list[dict[str, Any]] = []
        if isinstance(raw, Mapping):
            for label, count in raw.items():
                try:
                    number = int(float(count))
                except Exception:
                    continue
                items.append({"label": str(label).replace("_", " "), "count": number})
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, Mapping):
                    label = _value(item, "label", "reason", "name", "filter")
                    count = _value(item, "count", "total", "value")
                    if _present(label) and _present(count):
                        try:
                            items.append({"label": str(label), "count": int(float(count))})
                        except Exception:
                            continue
        return sorted(items, key=lambda item: int(item["count"]), reverse=True)[:3]

    def _post_market_context(self, trade_date: str) -> dict[str, Any]:
        manifest = self._latest_manifest(trade_date)
        snapshot_path = self._resolve_path(
            _value(manifest, "Snapshot_Manifest", "snapshot_manifest", default="")
        )
        snapshot = self._read_json_optional(snapshot_path) if snapshot_path else {}

        def find(*aliases: str) -> Any:
            for payload in (manifest, snapshot):
                found = self._nested_find(payload, aliases)
                if _present(found):
                    return found
            return ""

        reconciliation = snapshot.get("reconciliation") if isinstance(snapshot.get("reconciliation"), dict) else {}
        if not reconciliation and isinstance(manifest.get("reconciliation"), dict):
            reconciliation = dict(manifest["reconciliation"])

        result: dict[str, Any] = {
            "run_id": find("Run_ID", "run_id", "job_run_id"),
            "finished_at": find("Finished_At", "finished_at", "completed_at", "created_at"),
            "funnel_universe": find("Universe_Count", "universe_count", "symbols_requested"),
            "funnel_liquidity": find(
                "Liquidity_Passed_Count", "liquidity_passed_count",
                "liquidity_count", "symbols_after_liquidity",
            ),
            "funnel_technical": find(
                "Technical_Quality_Passed_Count", "technical_quality_passed_count",
                "technical_pass_count", "symbols_after_technical",
            ),
            "funnel_setup": find(
                "Setup_Valid_Count", "setup_valid_count", "valid_setup_count",
                "symbols_after_setup",
            ),
            "funnel_entry_ready": find(
                "Entry_Readiness_Passed_Count", "entry_readiness_passed_count",
                "entry_ready_count", "symbols_after_entry_readiness",
            ),
            "funnel_broker": find(
                "Broker_Available_Count", "broker_available_count",
                "broker_data_available_count", "broker_matched_count",
            ),
            "funnel_final": find(
                "Final_Watchlist_Count", "final_watchlist_count",
                "Final_Ready_Count", "final_ready_count",
            ),
            "buy_ready_count": find("BUY_READY_Count", "buy_ready_count", "BUY READY"),
            "buy_candidate_count": find(
                "BUY_CANDIDATE_Count", "buy_candidate_count",
                "BUY_ON_TRIGGER_Count", "buy_on_trigger_count", "BUY CANDIDATE",
            ),
            "watch_count": find("WATCH_Count", "watch_count", "WATCH"),
            "wait_count": find("WAIT_Count", "wait_count", "WAIT"),
            "avoid_count": find("AVOID_Count", "avoid_count", "AVOID"),
            "technical_data_date": find("Technical_Date", "technical_date", "trade_date"),
            "yahoo_data_date": find("Technical_Date", "technical_date", "trade_date"),
            "zapi_data_date": (
                reconciliation.get("validation_trade_date")
                or reconciliation.get("data_date")
                or reconciliation.get("latest_completed_session")
                or reconciliation.get("trade_date")
            ),
            "zapi_status": reconciliation.get("status") or find("Zapi_Status", "Reconciliation_Status", "zapi_status"),
            "zapi_request_count": reconciliation.get("request_count", find("Zapi_Request_Count", "zapi_request_count")),
            "zapi_request_cap": reconciliation.get("request_cap", find("Zapi_Request_Cap", "zapi_request_cap",)),
            "metadata_cache_status": reconciliation.get("metadata_cache_status", find("Zapi_Metadata_Cache_Status", "metadata_cache_status")),
            "metadata_cache_date": reconciliation.get("metadata_cache_date", find("Zapi_Metadata_Cache_Date", "metadata_cache_date")),
            "market_activity_cache_status": reconciliation.get("market_activity_cache_status", find("Zapi_Market_Activity_Cache_Status", "market_activity_cache_status")),
            "suspended_count": reconciliation.get("suspended_count", find("Suspended_Symbol_Count", "suspended_count")),
            "uma_count": reconciliation.get("uma_count", find("Uma_Symbol_Count", "uma_count")),
            "relisting_count": reconciliation.get("relisting_count", find("Relisting_Symbol_Count", "relisting_count")),
            "zapi_degraded": reconciliation.get("degraded", find("Zapi_Degraded", "zapi_degraded")),
            "degraded_reason": reconciliation.get("degraded_reason", find("Zapi_Degraded_Reason", "degraded_reason")),
            "zapi_note": reconciliation.get("reason") or reconciliation.get("note"),
            "stockbit_data_date": find("Broker_Date", "broker_date", "stockbit_data_date"),
            "stockbit_coverage": find(
                "Broker_Coverage", "broker_coverage", "broker_coverage_ratio",
                "matched_coverage",
            ),
            "stockbit_status": find("Broker_Status", "broker_status", "stockbit_status"),
            "market_outlook_status": find("Market_Outlook_Status", "market_outlook_status"),
            "final_watchlist_status": find("Final_Watchlist_Status", "final_watchlist_status"),
            "dominant_filters": self._dominant_filters(manifest, snapshot),
        }

        counts = [
            result.get("buy_ready_count"),
            result.get("buy_candidate_count"),
            result.get("watch_count"),
        ]
        if all(_present(value) for value in counts):
            result["final_ready_count"] = sum(int(float(value)) for value in counts)

        final_count = result.get("final_ready_count") or result.get("funnel_final")
        if _present(final_count):
            result["screening_interpretation"] = (
                "Kandidat final tersedia dan dapat diteruskan ke Final Watchlist."
                if int(float(final_count)) > 0
                else "Belum ada kandidat aktif yang siap diteruskan ke Final Watchlist."
            )
        return {key: value for key, value in result.items() if _present(value)}

    @staticmethod
    def _artifact_pick(sources: Iterable[Mapping[str, Any]], *aliases: str) -> Any:
        for source in sources:
            found = _value(source, *aliases, default="")
            if _present(found):
                return found
        return ""

    @classmethod
    def _participants(cls, sources: Iterable[Mapping[str, Any]], side: str) -> list[dict[str, Any]]:
        source_list = list(sources)
        prefix = "BUYER" if side.upper() == "BUYER" else "SELLER"
        result: list[dict[str, Any]] = []
        for index in range(1, 4):
            broker = cls._artifact_pick(
                source_list,
                f"TOP_{prefix}_{index}", f"TOP {prefix} {index}",
                f"{prefix}_{index}", f"{prefix} {index}",
            )
            if not _present(broker):
                continue
            item = {
                "broker": broker,
                "value": cls._artifact_pick(
                    source_list,
                    f"TOP_{prefix}_{index}_VALUE", f"TOP {prefix} {index} VALUE",
                    f"{prefix}_{index}_VALUE", f"{prefix} {index} VALUE",
                    f"TOP_{prefix}_{index}_NET_VALUE",
                ),
                "avg_price": cls._artifact_pick(
                    source_list,
                    f"TOP_{prefix}_{index}_AVG", f"TOP_{prefix}_{index}_AVG_PRICE",
                    f"TOP {prefix} {index} AVG", f"{prefix}_{index}_AVG_PRICE",
                ),
                "classification": cls._artifact_pick(
                    source_list,
                    f"TOP_{prefix}_{index}_TYPE", f"TOP_{prefix}_{index}_ORIGIN",
                    f"TOP_{prefix}_{index}_FOREIGN_LOCAL",
                    f"TOP {prefix} {index} TYPE",
                ),
            }
            result.append({key: value for key, value in item.items() if _present(value)})
        if result:
            return result
        broker = cls._artifact_pick(source_list, f"TOP_{prefix}", f"TOP {prefix}")
        return [{"broker": broker}] if _present(broker) else []

    def _enrich_watchlist_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        decision_map = self._symbol_map(self.output_root / "decision" / "FINAL_DECISION_V3.csv")
        entry_map = self._symbol_map(self.output_root / "exit" / "ENTRY_PLANS.csv")
        broker_map = self._symbol_map(self.output_root.parent / "input" / "FINAL_DECISION_V2.csv")
        technical_map = self._directory_symbol_map(self.output_root / "technical")
        candidate_map = self._directory_symbol_map(self.output_root / "candidates")

        enriched: list[dict[str, Any]] = []
        for row in rows:
            current = dict(row)
            symbol = self._symbol(current.get("symbol"))
            sources = [
                current,
                decision_map.get(symbol, {}),
                candidate_map.get(symbol, {}),
                technical_map.get(symbol, {}),
                broker_map.get(symbol, {}),
                entry_map.get(symbol, {}),
            ]

            mappings: dict[str, tuple[str, ...]] = {
                "trend": (
                    "Trend", "Trend_State", "Trend_Direction", "Technical_Trend",
                    "Trend_Final", "Trend_Label",
                ),
                "technical_quality": (
                    "Technical_Quality", "Technical_Quality_Score",
                    "Technical_Score_Final", "Technical_Score",
                ),
                "entry_readiness": (
                    "Entry_Readiness", "Entry_Readiness_Score",
                    "Readiness_Score", "Execution_Readiness",
                ),
                "momentum_status": (
                    "Momentum_Status", "Momentum_State", "Momentum",
                    "Momentum_Label", "MACD_State",
                ),
                "rsi": ("RSI", "RSI_14", "RSI14"),
                "volume_description": (
                    "Volume_Status", "Volume_State", "Volume_Confirmation",
                    "Volume_Description",
                ),
                "volume_ratio_ma20": (
                    "Volume_Ratio_MA20", "Volume_MA20_Ratio",
                    "Volume_Ratio", "Vol_Ratio_MA20",
                ),
                "last_price": (
                    "Last_Price", "Current_Price", "Close", "Price",
                ),
                "entry_distance_pct": (
                    "Entry_Distance_Pct", "Distance_To_Entry_Pct",
                    "Entry_Zone_Distance_Pct",
                ),
                "active_stop_loss": (
                    "Active_Stop_Loss", "activeStopLoss", "Initial_Stop", "Stop_Loss",
                ),
                "technical_status": (
                    "Technical_Status", "Plan_Status", "Execution_Status", "Technical_State",
                ),
                "broker_status": (
                    "Broker_Confirmation", "Broker_State", "Broker_Status",
                ),
                "broker_direction": (
                    "Broker_Direction_Final", "Broker_Direction",
                    "Broker_Flow_Direction",
                ),
                "broker_confidence": (
                    "Broker_Confidence_Final", "Broker_Confidence",
                    "Broker_Score",
                ),
                "broker_net_flow": ("NET_FLOW", "Net_Flow", "Net Flow"),
                "broker_buy_ratio": ("BUY_RATIO", "Buy_Ratio", "Buy Ratio"),
                "broker_sell_ratio": ("SELL_RATIO", "Sell_Ratio", "Sell Ratio"),
                "broker_alignment": (
                    "Broker_Alignment", "Flow_Status", "Flow_Alignment",
                    "Broker_Flow_Status",
                ),
                "avg_buyer_price": (
                    "AVG_BUYER_PRICE", "Average_Buyer_Price",
                    "Bandar_Buy_Cost", "Buyer_Weighted_Avg",
                ),
                "avg_seller_price": (
                    "AVG_SELLER_PRICE", "Average_Seller_Price",
                    "Bandar_Sell_Cost", "Seller_Weighted_Avg",
                ),
                "distance_to_buyer_avg_pct": (
                    "DISTANCE_TO_BUY_COST", "Distance_To_Buyer_Avg_Pct",
                    "Distance_To_Buy_Cost_Pct",
                ),
                "broker_raw_coverage": (
                    "Raw_Coverage", "Broker_Raw_Coverage",
                    "Broker_Coverage", "Coverage_Ratio",
                ),
                "buyer_concentration": ("BUYER_CONCENTRATION", "Buyer_Concentration"),
                "seller_concentration": ("SELLER_CONCENTRATION", "Seller_Concentration"),
                "broker_pattern": (
                    "BROKER_ACCDIST", "Broker_AccDist", "BROKER_PATTERN", "Broker_Pattern",
                ),
                "bandar_buy_cost": (
                    "Bandar_Buy_Cost", "AVG_BUYER_PRICE", "Weighted_Broker_Buy_Cost",
                ),
                "distance_to_buy_cost": (
                    "DISTANCE_TO_BUY_COST", "Distance_To_Buy_Cost_Pct",
                    "Distance_To_Buyer_Avg_Pct",
                ),
                "execution_state": (
                    "Execution_State", "Entry_Status", "Trade_Plan_Status",
                ),
                "phase": ("Phase", "Setup_Phase", "Execution_Status", "Plan_Status"),
                "support": ("Support_Level", "Support", "Technical_Support"),
                "resistance": (
                    "Nearest_Resistance", "Minor_Resistance", "Resistance_Level", "Resistance",
                ),
                "fib_status": ("Fibonacci_Status", "Fib_Status", "FIB_STATUS", "Target_Fib_Status"),
                "swing_high": ("Swing_High", "Valid_Swing_High"),
                "swing_low": ("Swing_Low", "Valid_Swing_Low"),
                "trigger_description": (
                    "Trigger_Description", "Entry_Trigger", "Trigger",
                    "Execution_Trigger",
                ),
                "main_reason_technical": (
                    "Main_Reason_Technical", "Technical_Reason",
                    "Reason_Technical",
                ),
                "main_reason_broker": (
                    "Main_Reason_Broker", "Broker_Reason",
                    "Reason_Broker",
                ),
                "main_reason_entry": (
                    "Main_Reason_Entry", "Entry_Reason",
                    "Readiness_Reason", "Reason_Entry",
                ),
                "invalidation": (
                    "Invalidation", "Invalidation_Condition",
                    "Setup_Invalidation", "Exit_Invalidation",
                ),
                "foreign_buy": ("Foreign_Buy", "FOREIGN_BUY"),
                "foreign_sell": ("Foreign_Sell", "FOREIGN_SELL"),
                "foreign_net": ("Foreign_Net", "FOREIGN_NET", "Foreign_Net_Flow"),
                "exchange_status": ("Exchange_Status", "Market_Activity_Status", "Bursa_Status"),
                "exchange_veto": ("Exchange_Veto", "Veto", "Veto_Reason"),
                "risk_flags": ("Risk_Flags", "Exchange_Risk_Flags", "Market_Risk_Flags"),
                "exchange_history_candles": ("Exchange_History_Candles", "History_Candle_Count"),
            }
            for target, aliases in mappings.items():
                if target == "broker_status" and str(current.get(target, "")).upper() in {"AVAILABLE", "MISSING"}:
                    current[target] = ""
                if not _present(current.get(target)):
                    found = self._artifact_pick(sources, *aliases)
                    if _present(found):
                        current[target] = found

            # An explicit empty list from the exact PRIMARY raw snapshot means
            # "no primary participant available"; do not replace it with a
            # potentially unrelated canonical/TODAY participant list.
            if "top_buyers" not in current or current.get("top_buyers") is None:
                current["top_buyers"] = self._participants(sources, "BUYER")
            if "top_sellers" not in current or current.get("top_sellers") is None:
                current["top_sellers"] = self._participants(sources, "SELLER")

            if not _present(current.get("waiting_triggers")):
                waiting: list[str] = []
                for index in range(1, 4):
                    waiting.extend(_split_items(self._artifact_pick(
                        sources,
                        f"Waiting_Trigger_{index}", f"Trigger_{index}",
                        f"Pending_Condition_{index}",
                    )))
                if not waiting:
                    waiting.extend(_split_items(self._artifact_pick(
                        sources,
                        "Waiting_Triggers", "Pending_Conditions",
                        "Execution_Conditions", "Trigger_Conditions",
                    )))
                current["waiting_triggers"] = waiting[:3]

            if not _present(current.get("risk_items")):
                risk_items: list[str] = []
                for index in range(1, 4):
                    risk_items.extend(_split_items(self._artifact_pick(
                        sources, f"Risk_{index}", f"Main_Risk_{index}",
                    )))
                if not risk_items:
                    risk_items = _split_items(current.get("main_risk") or self._artifact_pick(
                        sources, "Main_Risk", "Risk_Note", "Warnings",
                    ))
                current["risk_items"] = risk_items

            current.setdefault("analysis_date", current.get("trade_date", ""))
            if not _present(current.get("engine_final_reason")):
                current["engine_final_reason"] = current.get("main_reason", "")
            if not _present(current.get("active_stop_loss")):
                current["active_stop_loss"] = current.get("stop_loss", "")
            if not _present(current.get("technical_status")):
                current["technical_status"] = current.get("technical_state") or current.get("execution_state", "")
            if not _present(current.get("bandar_buy_cost")):
                current["bandar_buy_cost"] = current.get("avg_buyer_price", "")
            if not _present(current.get("distance_to_buy_cost")):
                current["distance_to_buy_cost"] = current.get("distance_to_buyer_avg_pct", "")
            if not _present(current.get("phase")):
                current["phase"] = current.get("execution_state") or current.get("setup", "")
            enriched.append(current)
        return enriched

    def build_market_outlook(self, data: dict[str, Any]) -> DailyReportArtifact:
        data = self._merge_missing(data, self._market_context(str(data.get("trade_date", ""))))
        fallback = {
            "main_reason": str(data.get("focus_tomorrow") or "Prioritaskan saham dengan setup valid dan broker flow mendukung."),
            "main_risk": str(data.get("avoid_guidance") or "Hindari mengejar harga di luar area entry."),
            "execution_note": "",
        }
        interpretation = self.interpreter.interpret(data, fallback)
        data["focus_tomorrow"] = interpretation.main_reason
        data["avoid_guidance"] = interpretation.main_risk
        data["ai_interpretation_status"] = interpretation.status
        data["ai_interpretation_source"] = interpretation.source
        if interpretation.warning:
            data.setdefault("warnings", []).append(interpretation.warning)
        return DailyReportArtifact("market_outlook", format_market_outlook(data))

    def build_post_market(self, data: dict[str, Any]) -> DailyReportArtifact:
        trade_date = str(data.get("trade_date", ""))
        data = self._merge_missing(data, self._market_context(trade_date))
        report_context = self._post_market_context(trade_date)
        for key, value in report_context.items():
            if _present(value):
                data[key] = value
        data.setdefault("finished_at", self._now_iso())
        requested = int(data.get("symbols_requested") or 0)
        loaded = int(data.get("symbols_loaded") or 0)
        valid = int(data.get("symbols_valid") or 0)
        not_loaded = max(0, requested - loaded)
        invalid = max(0, loaded - valid)
        data["symbols_not_loaded"] = not_loaded
        data["symbols_invalid"] = invalid
        if not _present(data.get("funnel_universe")) and requested:
            data["funnel_universe"] = requested

        status = str(data.get("process_status") or "").upper().replace("_", " ")
        zapi_status = str(data.get("zapi_status") or "").upper().replace("_", " ")
        if status == "SUCCESS" and (
            not_loaded > 0
            or invalid > 0
            or "WARNING" in zapi_status
            or _present(data.get("degraded_reason"))
        ):
            data["process_status"] = "SUCCESS_WITH_WARNING"

        notes: list[str] = []
        if not_loaded:
            notes.append(f"{not_loaded} saham tidak berhasil dimuat.")
        if invalid:
            notes.append(f"{invalid} saham gagal validasi.")
        if int(data.get("symbols_skipped") or 0):
            notes.append(f"{int(data.get('symbols_skipped') or 0)} saham dilewati.")
        if data.get("zapi_note") or data.get("degraded_reason"):
            notes.append(str(data.get("zapi_note") or data.get("degraded_reason")))
        if notes:
            data["data_note"] = " ".join(notes)
        data["data_impact"] = "TIDAK MATERIAL" if float(data.get("coverage") or 0) >= 90 else "MATERIAL"
        return DailyReportArtifact("post_market", format_post_market(data))

    def build_broker_summary(self, data: dict[str, Any]) -> tuple[DailyReportArtifact, DailyReportArtifact]:
        rows = [dict(row) for row in data.get("rows", [])]
        trade_date = str(data.get("trade_date", ""))
        path = self.output_root / "broker_summary" / f"broker_summary_{trade_date}.csv"
        self._write_csv(path, rows, BROKER_SUMMARY_COLUMNS)
        return (
            DailyReportArtifact("broker_summary", format_broker_summary(data)),
            DailyReportArtifact(
                "broker_summary_csv", "", attachment_path=path,
                caption="📎 CSV ringkasan broker terlampir.",
            ),
        )

    @staticmethod
    def _decision_key(value: Any) -> str:
        return str(value or "").upper().replace("_", " ").strip()

    @classmethod
    def _decision_bucket(cls, value: Any) -> str:
        decision = cls._decision_key(value)
        if decision in {"BUY", "BUY READY", "BUY CONFIRMED"}:
            return "BUY_READY"
        if decision in {"BUY CANDIDATE", "BUY ON TRIGGER"}:
            return "BUY_CANDIDATE"
        if decision in {"WATCH", "WATCH HIGH"}:
            return "WATCH"
        if decision == "WAIT":
            return "WAIT"
        if decision == "AVOID":
            return "AVOID"
        return "OTHER"

    @staticmethod
    def _ranking_value(row: Mapping[str, Any], key: str, default: float = 0.0) -> float:
        return _float(row.get(key), default)

    def build_final_watchlist(self, data: dict[str, Any]) -> list[DailyReportArtifact]:
        data = self._merge_missing(data, self._market_context(str(data.get("trade_date", ""))))
        data.setdefault("generated_at", self._now_iso())
        rows = self._enrich_watchlist_rows([dict(row) for row in data.get("rows", [])])
        # Spend the limited AI budget on the actual best candidates first.
        # `confidence` is Final_Score_V3 from the decision engine, so this is
        # presentation ordering only and never recalculates an engine score.
        ai_priority = {"BUY_READY": 0, "BUY_CANDIDATE": 1, "WATCH": 2}
        rows.sort(key=lambda row: (
            ai_priority.get(self._decision_bucket(row.get("decision")), 99),
            -self._ranking_value(row, "confidence"),
            int(_float(row.get("rank"), 9999)),
        ))
        interpreted: list[dict[str, Any]] = []
        for row in rows:
            current = dict(row)
            current.setdefault("trade_date", data.get("trade_date"))
            current.setdefault("provider", data.get("provider", ""))
            current.setdefault("source_mode", data.get("source_mode", ""))
            current.setdefault("coverage", data.get("coverage"))
            current.setdefault("zapi_status", data.get("zapi_status", ""))
            current.setdefault("reconciliation_status", data.get("reconciliation_status", ""))
            current.setdefault("exchange_status", data.get("exchange_status", "NORMAL"))
            current.setdefault("exchange_veto", data.get("exchange_veto", ""))
            current.setdefault("risk_flags", data.get("risk_flags", ""))
            current.setdefault("market_regime", data.get("market_regime", ""))
            current.setdefault("generated_at", data.get("generated_at"))
            exchange_status = str(current.get("exchange_status") or "NORMAL").upper()
            exchange_veto = str(current.get("exchange_veto") or current.get("veto") or "").upper()
            raw_flags = current.get("risk_flags") or ""
            flags = {
                str(item).strip().upper().replace("_", " ")
                for item in (raw_flags.split(",") if isinstance(raw_flags, str) else raw_flags)
                if str(item).strip()
            }
            if exchange_status in {"SUSPENDED", "BLOCKED"} or exchange_veto in {"SUSPENDED", "RELISTING_HISTORY_INSUFFICIENT"}:
                current["decision"] = "BLOCKED"
            elif "RELISTING" in flags and exchange_veto == "RELISTING_HISTORY_INSUFFICIENT":
                current["decision"] = "BLOCKED"
            elif "UMA" in flags and self._decision_bucket(current.get("decision")) == "BUY_READY":
                current["decision"] = "BUY CANDIDATE"
            fallback = {
                "main_reason": str(current.get("main_reason") or self._watchlist_reason(current)),
                "main_risk": str(current.get("main_risk") or self._watchlist_risk(current)),
                "execution_note": str(current.get("execution_note") or ""),
            }
            result = self.interpreter.interpret(current, fallback)
            current["main_reason"] = result.main_reason
            current["main_risk"] = result.main_risk
            current["execution_note"] = result.execution_note
            current["interpretation_source"] = result.source
            current["interpretation_status"] = result.status
            interpreted.append(current)

        allowed_buckets = {"BUY_READY", "BUY_CANDIDATE", "WATCH"}
        priority = {"BUY_READY": 0, "BUY_CANDIDATE": 1, "WATCH": 2}
        selected = [row for row in interpreted if self._decision_bucket(row.get("decision")) in allowed_buckets]
        selected.sort(key=lambda row: (
            priority.get(self._decision_bucket(row.get("decision")), 99),
            # Final_Score_V3 is the canonical cross-factor score produced by
            # the decision engine. Rank by it before presentation tie-breaks.
            -self._ranking_value(row, "confidence"),
            -self._ranking_value(row, "entry_readiness"),
            abs(self._ranking_value(row, "entry_distance_pct", 9999.0)),
            -self._ranking_value(row, "broker_confidence", self._ranking_value(row, "broker_score")),
            -self._ranking_value(row, "technical_quality", self._ranking_value(row, "technical_score")),
            -self._ranking_value(row, "risk_reward"),
            int(_float(row.get("rank"), 9999)),
        ))
        for index, row in enumerate(selected, start=1):
            row["rank"] = index

        trade_date = str(data.get("trade_date", ""))
        csv_path = self.output_root / "final_watchlist" / f"sde-final-watchlist-{trade_date}.csv"
        self._write_csv(
            csv_path,
            selected,
            FINAL_WATCHLIST_COLUMNS + ["interpretation_source", "interpretation_status"],
        )

        counts = {"BUY_READY": 0, "BUY_CANDIDATE": 0, "WATCH": 0, "WAIT": 0, "AVOID": 0, "OTHER": 0}
        for row in interpreted:
            bucket = self._decision_bucket(row.get("decision"))
            counts[bucket] = counts.get(bucket, 0) + 1

        detail_rows = [
            row for row in selected
            if self._decision_key(row.get("decision")) in FINAL_WATCHLIST_DETAIL_DECISIONS
        ]
        if self.max_watchlist_messages > 0:
            detail_rows = detail_rows[: self.max_watchlist_messages]

        summary_data = {
            **data,
            "rows": selected,
            "decision_counts": counts,
            "top_priority": detail_rows[:5],
            "csv_filename": csv_path.name,
        }
        artifacts: list[DailyReportArtifact] = [
            DailyReportArtifact("final_watchlist_summary", format_final_watchlist_summary(summary_data))
        ]
        historical_dir = Path(getattr(self, "historical_dir", "data/output/historical/by_symbol"))
        chart_output_root = Path(getattr(self, "chart_output_root", "output/final_watchlist"))
        for row in detail_rows:
            text = format_watchlist_detail(row)
            details = {"material_signature": self._material_signature(row)}
            chart: Path | None = None
            try:
                chart = generate_final_watchlist_chart(
                    row,
                    historical_dir=historical_dir,
                    output_dir=chart_output_root,
                    candle_limit=80,
                )
                details.update({"chart_status": "GENERATED", "chart_path": str(chart)})
            except Exception as exc:
                details.update({"chart_status": "FAILED_TEXT_FALLBACK", "chart_error": str(exc)})
                logging.getLogger(__name__).warning(
                    "Chart generation failed for %s: %s", row.get("symbol", ""), exc
                )
            artifacts.append(DailyReportArtifact(
                "final_watchlist_detail",
                text,
                symbol=str(row.get("symbol", "")).upper(),
                attachment_path=chart,
                caption=compact_final_watchlist_caption(text) if chart is not None else "",
                validation_details=details,
            ))
        artifacts.append(DailyReportArtifact(
            "final_watchlist_csv", "", attachment_path=csv_path,
            caption=(
                "📎 FINAL WATCHLIST LENGKAP\n\n"
                f"{len(selected)} saham masuk daftar:\n"
                f"• BUY READY: {counts['BUY_READY']}\n"
                f"• BUY CANDIDATE: {counts['BUY_CANDIDATE']}\n"
                f"• WATCH: {counts['WATCH']}\n\n"
                "CSV memuat seluruh saham aktif beserta ranking, teknikal,\n"
                "trade plan, broker flow, risiko, dan action."
            ),
        ))
        return artifacts

    @staticmethod
    def _csv_value(value: Any) -> Any:
        if isinstance(value, (list, tuple, dict)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return value

    @classmethod
    def _write_csv(cls, path: Path, rows: Iterable[dict[str, Any]], columns: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: cls._csv_value(row.get(key, "")) for key in columns})

    @staticmethod
    def _material_signature(row: Mapping[str, Any]) -> str:
        payload = "|".join(str(row.get(key, "")) for key in (
            "symbol", "trade_date", "setup", "entry_low", "entry_high",
            "active_stop_loss", "stop_loss", "target_1", "target_2",
        ))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _watchlist_reason(row: dict[str, Any]) -> str:
        technical = str(row.get("trend") or row.get("technical_state") or "belum terkonfirmasi").replace("_", " ").lower()
        broker = str(row.get("broker_direction") or row.get("broker_state") or "belum tersedia").replace("_", " ").lower()
        sector = str(row.get("sector_state", "belum tersedia")).replace("_", " ").lower()
        return f"Setup teknikal {technical}, broker {broker}, dan sektor {sector}."

    @staticmethod
    def _watchlist_risk(row: dict[str, Any]) -> str:
        stop = row.get("stop_loss")
        if stop not in (None, ""):
            return f"Setup batal jika harga menembus level stop loss {stop}."
        return "Entry hanya dilakukan setelah trigger valid; hindari mengejar harga di luar zona entry."
