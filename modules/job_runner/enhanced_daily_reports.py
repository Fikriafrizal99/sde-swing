from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from modules.ai_interpretation import GeminiInterpreter
from modules.telegram.daily_report_ui import (
    format_broker_multiday,
    format_broker_summary,
    format_market_outlook,
    format_post_market,
    format_watchlist_detail,
)


@dataclass(frozen=True)
class DailyReportArtifact:
    report_type: str
    text: str
    topic: str = "report"
    symbol: str = ""
    attachment_path: Path | None = None
    caption: str = ""


FINAL_WATCHLIST_COLUMNS = [
    "trade_date", "rank", "symbol", "decision", "confidence", "setup",
    "entry_low", "entry_high", "stop_loss", "target_1", "target_2",
    "risk_reward", "technical_score", "technical_state", "broker_score",
    "broker_state", "sector_state", "market_regime", "main_reason",
    "main_risk", "data_status", "source",
]

BROKER_SUMMARY_COLUMNS = [
    "trade_date", "symbol", "broker_state", "broker_score", "net_flow",
    "buy_ratio", "sell_ratio", "top_buyer", "top_seller", "broker_1d",
    "broker_3d", "broker_5d", "data_status", "source",
]

BROKER_MULTIDAY_COLUMNS = [
    "trade_date", "symbol", "state_1d", "state_3d", "state_5d", "state_10d",
    "state_20d", "overall_state", "broker_score", "coverage_days",
    "missing_days", "data_status", "source",
]


class EnhancedDailyReportBuilder:
    """Build the agreed Telegram UI and CSV artifacts from engine-owned data.

    Input values are treated as final engine facts. Gemini may only replace the
    narrative fields main_reason/main_risk/execution_note. The original rows are
    never mutated in place.
    """

    def __init__(
        self,
        output_root: str | Path = "data/output",
        interpreter: GeminiInterpreter | None = None,
        max_watchlist_messages: int = 5,
    ) -> None:
        self.output_root = Path(output_root)
        self.interpreter = interpreter or GeminiInterpreter()
        self.max_watchlist_messages = max(1, int(max_watchlist_messages))

    def build_all(self, bundle: dict[str, Any]) -> list[DailyReportArtifact]:
        artifacts: list[DailyReportArtifact] = []
        if bundle.get("market_outlook"):
            artifacts.append(self.build_market_outlook(dict(bundle["market_outlook"])))
        if bundle.get("post_market"):
            artifacts.append(self.build_post_market(dict(bundle["post_market"])))
        if bundle.get("broker_summary"):
            summary, csv_artifact = self.build_broker_summary(dict(bundle["broker_summary"]))
            artifacts.extend([summary, csv_artifact])
        if bundle.get("broker_multiday"):
            multiday, csv_artifact = self.build_broker_multiday(dict(bundle["broker_multiday"]))
            artifacts.extend([multiday, csv_artifact])
        if bundle.get("final_watchlist"):
            artifacts.extend(self.build_final_watchlist(dict(bundle["final_watchlist"])))
        return artifacts

    def build_market_outlook(self, data: dict[str, Any]) -> DailyReportArtifact:
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

    @staticmethod
    def build_post_market(data: dict[str, Any]) -> DailyReportArtifact:
        return DailyReportArtifact("post_market", format_post_market(data))

    def build_broker_summary(self, data: dict[str, Any]) -> tuple[DailyReportArtifact, DailyReportArtifact]:
        rows = [dict(row) for row in data.get("rows", [])]
        trade_date = str(data.get("trade_date", "unknown"))
        path = self.output_root / "broker_summary" / f"broker_summary_{trade_date}.csv"
        self._write_csv(path, rows, BROKER_SUMMARY_COLUMNS)
        return (
            DailyReportArtifact("broker_summary", format_broker_summary(data)),
            DailyReportArtifact(
                "broker_summary_csv", "", attachment_path=path,
                caption="📎 CSV ringkasan broker terlampir.",
            ),
        )

    def build_broker_multiday(self, data: dict[str, Any]) -> tuple[DailyReportArtifact, DailyReportArtifact]:
        rows = [dict(row) for row in data.get("rows", [])]
        interpreted_rows: list[dict[str, Any]] = []
        for row in rows:
            current = dict(row)
            fallback = {
                "main_reason": str(current.get("interpretation") or self._multiday_fallback(current)),
                "main_risk": str(current.get("main_risk") or "Histori yang belum lengkap menurunkan keyakinan interpretasi."),
                "execution_note": "",
            }
            result = self.interpreter.interpret(current, fallback)
            current["interpretation"] = result.main_reason
            current["interpretation_source"] = result.source
            interpreted_rows.append(current)
        data["rows"] = interpreted_rows
        trade_date = str(data.get("trade_date", "unknown"))
        path = self.output_root / "broker_multiday" / f"broker_multiday_{trade_date}.csv"
        self._write_csv(path, interpreted_rows, BROKER_MULTIDAY_COLUMNS + ["interpretation", "interpretation_source"])
        return (
            DailyReportArtifact("broker_multiday", format_broker_multiday(data)),
            DailyReportArtifact(
                "broker_multiday_csv", "", attachment_path=path,
                caption="📎 CSV broker multi-day terlampir.",
            ),
        )

    def build_final_watchlist(self, data: dict[str, Any]) -> list[DailyReportArtifact]:
        rows = [dict(row) for row in data.get("rows", [])]
        interpreted: list[dict[str, Any]] = []
        for row in rows:
            current = dict(row)
            current.setdefault("trade_date", data.get("trade_date"))
            current.setdefault("provider", data.get("provider", "NOT_CONFIGURED"))
            current.setdefault("source_mode", data.get("source_mode", "NOT_CONFIGURED"))
            current.setdefault("coverage", data.get("coverage", 0))
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

        trade_date = str(data.get("trade_date", "unknown"))
        csv_path = self.output_root / "final_watchlist" / f"final_watchlist_{trade_date}.csv"
        self._write_csv(
            csv_path,
            interpreted,
            FINAL_WATCHLIST_COLUMNS + ["interpretation_source", "interpretation_status", "execution_note"],
        )

        allowed = {"BUY", "BUY CONFIRMED", "BUY_CANDIDATE", "BUY CANDIDATE", "WATCH_HIGH", "WATCH HIGH", "WATCH"}
        priority = {
            "BUY": 0, "BUY CONFIRMED": 0, "BUY_CANDIDATE": 1, "BUY CANDIDATE": 1,
            "WATCH_HIGH": 2, "WATCH HIGH": 2, "WATCH": 3,
        }
        selected = [row for row in interpreted if str(row.get("decision", "")).upper() in allowed]
        selected.sort(key=lambda row: (
            priority.get(str(row.get("decision", "")).upper(), 99),
            -float(row.get("confidence", 0) or 0),
            int(row.get("rank", 9999) or 9999),
        ))
        artifacts = [
            DailyReportArtifact(
                "final_watchlist_detail",
                format_watchlist_detail(row),
                symbol=str(row.get("symbol", "")).upper(),
            )
            for row in selected[: self.max_watchlist_messages]
        ]
        artifacts.append(DailyReportArtifact(
            "final_watchlist_csv", "", attachment_path=csv_path,
            caption="📎 Final Watchlist lengkap terlampir.",
        ))
        return artifacts

    @staticmethod
    def _write_csv(path: Path, rows: Iterable[dict[str, Any]], columns: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in columns})

    @staticmethod
    def _multiday_fallback(row: dict[str, Any]) -> str:
        one = str(row.get("state_1d", "NO_DATA")).upper()
        three = str(row.get("state_3d", "NO_DATA")).upper()
        five = str(row.get("state_5d", "NO_DATA")).upper()
        positive = sum("ACC" in state for state in (one, three, five))
        negative = sum("DIST" in state for state in (one, three, five))
        if positive >= 2:
            return "Akumulasi broker terlihat konsisten pada mayoritas horizon utama."
        if negative >= 2:
            return "Distribusi broker terlihat konsisten pada mayoritas horizon utama."
        return "Arah broker masih campuran dan belum konsisten antarhorizon."

    @staticmethod
    def _watchlist_reason(row: dict[str, Any]) -> str:
        technical = str(row.get("technical_state", "belum terkonfirmasi")).replace("_", " ").lower()
        broker = str(row.get("broker_state", "belum tersedia")).replace("_", " ").lower()
        sector = str(row.get("sector_state", "belum tersedia")).replace("_", " ").lower()
        return f"Setup teknikal {technical}, broker {broker}, dan sektor {sector}."

    @staticmethod
    def _watchlist_risk(row: dict[str, Any]) -> str:
        stop = row.get("stop_loss")
        if stop not in (None, ""):
            return f"Setup batal jika harga menembus level stop loss {stop}."
        return "Entry hanya dilakukan setelah trigger valid; hindari mengejar harga di luar zona entry."
