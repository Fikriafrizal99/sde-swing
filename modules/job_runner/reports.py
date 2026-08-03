from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from swing_utils import find_col, read_json
from modules.broker_bridge.broker_raw import validate_broker_raw
from modules.telegram.professional_ui import (
    UiConfig,
    broker_flow_summary,
    format_closing_bell,
    format_data_warning,
    format_daily_signal_recap,
    format_market_outlook,
    format_post_market_summary,
    format_signal_detail,
    format_watchlist,
    rank_watchlist,
    select_final_watchlist_rows,
    unique_final_decisions,
)

from .runtime import RunnerContext, latest_matching_file, resolve, write_json


@dataclass
class ReportPayload:
    report_type: str
    filename: str
    text: str
    topic: str = "default"
    symbol: str = ""
    signal_status: str = ""
    signal_version: str = ""
    material_signature: str = ""

    @property
    def signature(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def value(row: pd.Series | dict[str, Any], *aliases: str, default: Any = "") -> Any:
    if isinstance(row, dict):
        for alias in aliases:
            if alias in row and row[alias] not in (None, ""):
                return row[alias]
        return default
    for alias in aliases:
        for col in row.index:
            if str(col).strip().lower().replace("_", " ") == alias.lower().replace("_", " "):
                current = row[col]
                if pd.notna(current) and str(current).strip() != "":
                    return current
    return default


def fmt_num(item: Any, decimals: int = 2, missing: str = "DATA_NOT_AVAILABLE") -> str:
    try:
        if item is None or str(item).strip() == "" or str(item).lower() == "nan":
            return missing
        return f"{float(item):,.{decimals}f}".replace(",", ".")
    except Exception:
        return str(item) if str(item).strip() else missing


def fmt_pct(item: Any) -> str:
    try:
        return f"{float(item):+.2f}%"
    except Exception:
        return "DATA_NOT_AVAILABLE"


def html_escape(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def latest_date_from_csv(path: Path, *aliases: str) -> str:
    df = load_csv(path)
    if df.empty:
        return ""
    col = find_col(df, *(aliases or ("Date",)))
    if not col:
        return ""
    parsed = pd.to_datetime(df[col], errors="coerce").dropna()
    return parsed.max().date().isoformat() if not parsed.empty else ""


def write_payloads(ctx: RunnerContext, payloads: list[ReportPayload]) -> list[Path]:
    folder = ctx.previews_root / ctx.trade_date.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for payload in payloads:
        header = [
            f"Run ID: {ctx.run_id}",
            f"Trade Date: {ctx.trade_date.isoformat()}",
            f"Report Type: {payload.report_type}",
            "",
        ]
        text = "\n".join(header) + payload.text.strip() + "\n"
        path = folder / payload.filename
        path.write_text(text, encoding="utf-8")
        run_scoped = folder / f"{ctx.run_id}_{payload.filename}"
        run_scoped.write_text(text, encoding="utf-8")
        written.append(path)
    write_json(folder / f"{ctx.run_id}_preview_manifest.json", {
        "run_id": ctx.run_id,
        "job": ctx.job,
        "trade_date": ctx.trade_date.isoformat(),
        "files": [str(path) for path in written],
        "report_types": [p.report_type for p in payloads],
    })
    return written


def load_run_manifest(ctx: RunnerContext, run_id: str | None = None) -> dict[str, Any]:
    rid = run_id or ctx.run_id
    return read_json(ctx.path("manifest_dir", "data/output/manifests") / f"SWING_RUN_MANIFEST_{rid}.json")


def ui_config(ctx: RunnerContext) -> UiConfig:
    telegram_cfg = read_json(ctx.path("telegram_config", "config/telegram.json"))
    raw = telegram_cfg.get("telegram_ui", {})
    scheduler_telegram = ctx.scheduler_config.get("telegram", {})
    if "max_message_length" not in raw and scheduler_telegram.get("maximum_message_length"):
        raw = {**raw, "max_message_length": int(scheduler_telegram["maximum_message_length"])}
    return UiConfig.from_dict(raw)


def _broker_raw_valid(path: Path, trade_date: str) -> tuple[bool, pd.DataFrame]:
    valid, frame, _warning = validate_broker_raw(path, trade_date)
    return valid, frame


def load_broker_raw(ctx: RunnerContext, trade_date: str) -> pd.DataFrame:
    """Load normalized per-broker detail exported by Tampermonkey.

    Broker Raw is presentation-only. The loader accepts known column aliases,
    normalizes BUY/SELL labels, deduplicates repeated browser captures, and
    derives average price from value/lot when AVG_PRICE is empty. It does not
    alter Broker Summary or any decision score.
    """
    target = ctx.path("broker_raw_latest", "data/input/broker/BROKER_RAW_LATEST.csv")
    valid, frame = _broker_raw_valid(target, trade_date)
    if valid:
        return frame

    downloads_text = str(ctx.config.get("broker", {}).get("downloads_dir", "%USERPROFILE%/Downloads"))
    downloads = Path(os.path.expandvars(downloads_text)).expanduser()
    candidates: list[Path] = []
    exact = downloads / f"BROKER_RAW_COMBINED_{trade_date}.csv"
    if exact.exists():
        candidates.append(exact)
    if downloads.exists():
        candidates.extend(
            path for path in sorted(downloads.glob("BROKER_RAW_COMBINED_*.csv"), key=lambda item: item.stat().st_mtime, reverse=True)
            if path not in candidates
        )
    for candidate in candidates:
        valid, frame = _broker_raw_valid(candidate, trade_date)
        if not valid:
            continue
        if not ctx.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)
            archive = target.parent / "archive" / f"BROKER_RAW_{trade_date}_{ctx.run_id}.csv"
            archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, archive)
        return frame
    return pd.DataFrame()


def _technical_as_watch_decisions(candidates: pd.DataFrame) -> pd.DataFrame:
    """Create presentation-only WATCH rows for the 16:30 technical snapshot.

    This does not mutate or replace FINAL_DECISION_V3. Broker-confirmed decisions
    remain reserved for the 18:00 final-watchlist job.
    """
    if candidates.empty:
        return candidates.copy()
    work = candidates.copy()
    if "Symbol" not in work.columns:
        symbol_col = find_col(work, "Symbol", "EMITEN", "Ticker")
        if symbol_col:
            work["Symbol"] = work[symbol_col]
    work["Decision_V3"] = "WATCH"
    if "Final_Score_V3" not in work.columns:
        score_col = find_col(work, "Technical_Score")
        work["Final_Score_V3"] = work[score_col] if score_col else 0
    return unique_final_decisions(work)


def data_quality_payload(ctx: RunnerContext, run_manifest: dict[str, Any] | None = None) -> list[ReportPayload]:
    manifest = run_manifest or load_run_manifest(ctx)
    quality = str(manifest.get("Data_Quality_Status", "VALID")).upper()
    warnings = [str(x) for x in manifest.get("Warnings", []) if str(x).strip()]
    data_source = str(manifest.get("Data_Source", "")).upper()
    fallback = bool(manifest.get("Fallback_Used", False))
    broker_override = bool(manifest.get("Broker_Date_Override", False))
    has_issue = quality != "VALID" or warnings or fallback or broker_override or data_source == "OFFLINE_FIXTURE"
    if not has_issue:
        return []
    text = format_data_warning(
        run_id=manifest.get("Run_ID", ctx.run_id),
        expected_date=manifest.get("Latest_Expected_Trading_Date", manifest.get("Technical_Date", ctx.trade_date.isoformat())),
        latest_valid_date=manifest.get("Historical_Latest_Valid_Date", manifest.get("Technical_Date", "DATA_NOT_AVAILABLE")),
        broker_date=manifest.get("Broker_Date", "DATA_NOT_AVAILABLE"),
        fallback_used=fallback,
        broker_override=broker_override,
        data_status=quality,
        warnings=warnings,
        data_source=data_source,
        config=ui_config(ctx),
    )
    return [ReportPayload("data_warning", "data_warning.txt", text, topic="system")]


def _global_market_snapshot(ctx: RunnerContext) -> dict[str, Any]:
    return read_json(resolve("data/output/global_market") / ctx.trade_date.isoformat() / "global_market_snapshot.json")


def _global_market_lines(snapshot: dict[str, Any]) -> list[str]:
    if not snapshot:
        return [
            "Global Market Snapshot: DATA_NOT_AVAILABLE",
            "Global sentiment: INSUFFICIENT_DATA",
            "- DATA_NOT_AVAILABLE",
        ]
    sentiment = snapshot.get("global_sentiment", {})
    lines = [
        f"Global Market Snapshot: {snapshot.get('snapshot_id', 'DATA_NOT_AVAILABLE')}",
        f"Global sentiment: {sentiment.get('sentiment_state', 'INSUFFICIENT_DATA')} "
        f"(coverage {float(sentiment.get('coverage_ratio', 0.0)):.0%}; score {float(sentiment.get('sentiment_score', 0.0)):+.2f})",
        f"Alasan global: {sentiment.get('reason', 'DATA_NOT_AVAILABLE')}",
        "Global data:",
    ]
    categories = [
        ("US_INDEX", "Indeks Amerika"),
        ("ASIA_INDEX", "Indeks Asia"),
        ("CURRENCY", "Currency"),
        ("COMMODITY", "Komoditas"),
    ]
    rows = list(snapshot.get("instruments", []))
    for category, label in categories:
        group = [row for row in rows if str(row.get("category", "")) == category]
        if not group:
            continue
        lines.append(label + ":")
        for row in group:
            status = str(row.get("freshness_status", "DATA_NOT_AVAILABLE"))
            name = str(row.get("display_name", row.get("instrument", "")))
            symbol = str(row.get("yahoo_symbol", ""))
            if status in {"VALID", "DELAYED_ACCEPTED"} and row.get("close") is not None:
                lines.append(
                    f"- {name} ({symbol}): {fmt_num(row.get('close'), 2)} "
                    f"({fmt_pct(row.get('change_pct'))}) | {row.get('market_date', 'DATA_NOT_AVAILABLE')} | {status}"
                )
            else:
                lines.append(f"- {name} ({symbol}): DATA_NOT_AVAILABLE | {status}")
    lines.append("Catatan: global sentiment hanya konteks Market Outlook dan belum mengubah scoring saham.")
    return lines


def market_outlook_payload(
    ctx: RunnerContext,
    global_snapshot: dict[str, Any] | None = None,
    market_status: dict[str, Any] | None = None,
) -> list[ReportPayload]:
    market_status = market_status or read_json(
        ctx.previews_root.parent / "market_regime" / ctx.trade_date.isoformat() / "market_outlook_regime.json"
    )
    if not market_status:
        market_status = read_json(ctx.path("decision_output_dir", "data/output/decision") / "MARKET_STATUS.json")
    decisions = load_csv(ctx.path("decision_output_dir", "data/output/decision") / "FINAL_DECISION_V3.csv")
    global_snapshot = global_snapshot if global_snapshot is not None else _global_market_snapshot(ctx)
    regime = str(market_status.get("market_regime", "UNKNOWN")).upper()
    global_state = str(global_snapshot.get("global_sentiment", {}).get("sentiment_state", "INSUFFICIENT_DATA")) if global_snapshot else "INSUFFICIENT_DATA"

    if regime in {"STRONG BEARISH", "BEARISH"} or global_state == "RISK_OFF":
        plan = "Bersikap defensif. Tunggu konfirmasi kuat, kurangi frekuensi entry, dan batasi ukuran posisi pada kandidat terbaik."
        risks = [
            "Tekanan IHSG atau global dapat memperlemah breakout.",
            "Hindari saham tidak likuid dan setup dengan risk/reward rendah.",
            "Jangan melakukan averaging down pada setup yang sudah invalid.",
        ]
    elif regime == "EARLY BEARISH":
        plan = "Momentum pasar mulai melemah. Prioritaskan perlindungan modal dan hanya pantau setup dengan broker flow sangat kuat."
        risks = [
            "Kegagalan bertahan di area support IHSG.",
            "False rebound pada saham yang masih berada dalam tekanan.",
            "Pelemahan rupiah dan likuiditas pasar.",
        ]
    elif regime in {"STRONG BULLISH", "BULLISH"} and global_state != "RISK_OFF":
        plan = "Cari peluang secara selektif agresif pada breakout valid atau pullback sehat dengan volume dan broker flow mendukung."
        risks = [
            "Jangan mengejar harga yang sudah terlalu jauh dari area entry.",
            "Waspadai profit taking setelah kenaikan cepat.",
            "Validasi broker flow sebelum menaikkan ukuran posisi.",
        ]
    elif regime == "EARLY BULLISH" and global_state != "RISK_OFF":
        plan = "Pasar menunjukkan pemulihan, tetapi tren belum sepenuhnya terkonfirmasi. Fokus pada kandidat kuat dan masuk hanya setelah trigger valid."
        risks = [
            "Pemulihan IHSG dapat gagal jika MA20 belum menguat di atas MA50.",
            "False breakout sebelum breadth pasar benar-benar meluas.",
            "Mengejar saham yang sudah terlalu jauh dari area entry.",
        ]
    else:
        plan = "Market belum memberikan arah yang kuat. Fokus pada kandidat terbaik dan tunggu area entry atau trigger yang valid sebelum melakukan eksekusi."
        risks = [
            "False breakout saat market masih sideways.",
            "Perubahan sentimen global secara mendadak.",
            "Pelemahan rupiah dan likuiditas pasar.",
            "Mengejar saham yang sudah terlalu jauh dari area entry.",
        ]

    focus = [
        "Setup teknikal dengan trend dan momentum yang selaras.",
        "Harga masih dekat area entry atau sedang membentuk pullback sehat.",
        "Broker flow menunjukkan akumulasi dengan risk/reward yang layak.",
    ]
    text = format_market_outlook(
        trade_date=ctx.trade_date,
        market_status=market_status,
        global_snapshot=global_snapshot,
        broker_flow=broker_flow_summary(decisions),
        plan_summary=plan,
        focus=focus,
        risks=risks,
        config=ui_config(ctx),
    )
    return [ReportPayload("market_outlook", "market_outlook.txt", text, topic="market_outlook")]


def _snapshot_from_manifest(run_manifest: dict[str, Any]) -> dict[str, Any]:
    snapshot_path = str(run_manifest.get("Snapshot_Manifest", ""))
    return read_json(Path(snapshot_path)) if snapshot_path else {}


def post_market_payloads(ctx: RunnerContext, run_manifest: dict[str, Any] | None = None) -> list[ReportPayload]:
    manifest = run_manifest or load_run_manifest(ctx)
    snapshot = _snapshot_from_manifest(manifest)
    output_paths = snapshot.get("output_paths", {}) if snapshot else {}
    technical_path = Path(str(output_paths.get("technical_features") or ctx.path("technical_output_dir", "data/output/technical") / "latest_technical_features.csv"))
    candidate_top = int(ctx.config.get("candidate", {}).get("top", 40))
    candidates_path = Path(str(
        output_paths.get("technical_candidates")
        or ctx.path("candidate_output_dir", "data/output/candidates") / f"technical_candidates_top{candidate_top}.csv"
    ))
    technical = load_csv(technical_path)
    candidates = load_csv(candidates_path)
    trade_date = (
        str(snapshot.get("trade_date") or manifest.get("Technical_Date") or "")
        or latest_date_from_csv(technical_path, "Date", "Technical_Data_Date")
        or ctx.trade_date.isoformat()
    )
    market_status = read_json(ctx.path("decision_output_dir", "data/output/decision") / "MARKET_STATUS.json")
    ihsg = load_csv(ctx.path("ihsg_csv", "data/input/IHSG.csv"))
    warnings = data_quality_payload(ctx, manifest)
    text = format_post_market_summary(
        trade_date=trade_date,
        technical=technical,
        candidates=candidates,
        market_status=market_status,
        ihsg=ihsg,
        config=ui_config(ctx),
    )
    return [*warnings, ReportPayload("post_market", "post_market.txt", text, topic="post_market")]


def final_watchlist_payloads(ctx: RunnerContext, run_manifest: dict[str, Any] | None = None) -> list[ReportPayload]:
    decisions = load_csv(ctx.path("decision_output_dir", "data/output/decision") / "FINAL_DECISION_V3.csv")
    entry = load_csv(ctx.path("exit_output_dir", "data/output/exit") / "ENTRY_PLANS.csv")
    manifest = run_manifest or load_run_manifest(ctx)
    trade_date = str(manifest.get("Technical_Date", ctx.trade_date.isoformat()))
    broker_raw = load_broker_raw(ctx, trade_date)
    payloads = data_quality_payload(ctx, manifest)
    cfg = ui_config(ctx)

    watchlist_text = format_watchlist(
        trade_date=trade_date,
        decisions=decisions,
        entry_plans=entry,
        broker_raw=broker_raw,
        config=cfg,
    )
    if broker_raw.empty:
        watchlist_text += "\n\n⚠️ File BROKER_RAW belum tersedia. Nama broker ditampilkan dari summary, tetapi nilai dan average price per broker belum dapat ditampilkan."
    payloads.append(ReportPayload("final_watchlist", "final_watchlist.txt", watchlist_text, topic="final_watchlist"))

    ranked = select_final_watchlist_rows(decisions, entry, cfg)
    if not ranked.empty and "__report_status" in ranked.columns:
        ranked = ranked[ranked["__report_status"].isin({"BUY CONFIRMED", "BUY CANDIDATE"})]
    else:
        ranked = ranked.head(0)
    ranked = ranked.head(int(ctx.scheduler_config.get("final_watchlist", {}).get("max_detail_symbols", 5)))
    symbol_col = find_col(ranked, "Symbol", "EMITEN", "Ticker")
    decision_col = find_col(ranked, "Decision_V3", "Decision")
    for _, row in ranked.iterrows():
        symbol = str(value(row, symbol_col or "Symbol", default="?")).upper()
        plan = pd.Series(dtype=object)
        if not entry.empty:
            entry_symbol_col = find_col(entry, "Symbol", "EMITEN")
            if entry_symbol_col:
                found = entry[entry[entry_symbol_col].astype(str).str.upper() == symbol]
                if not found.empty:
                    plan = found.iloc[0]
        detail_text = format_signal_detail(
            trade_date=trade_date,
            row=row,
            entry_plan=plan,
            broker_raw=broker_raw,
            config=cfg,
        )
        payloads.append(ReportPayload(
            "signal_detail",
            f"signal_detail_{symbol}.txt",
            detail_text,
            topic="signal_detail",
            symbol=symbol,
            signal_status=str(value(row, decision_col or "Decision_V3", default="UNKNOWN")).upper(),
            signal_version=str(manifest.get("Technical_Snapshot_ID") or manifest.get("Run_ID") or ctx.run_id),
            material_signature="|".join([
                symbol,
                str(value(row, decision_col or "Decision_V3", default="")),
                str(value(row, "Final_Score_V3", "Final_Score", default="")),
                str(value(row, "Broker_Score", default="")),
                str(len(broker_raw)),
            ]),
        ))
    return payloads


def broker_waiting_payload(ctx: RunnerContext, detail: dict[str, Any]) -> list[ReportPayload]:
    text = format_data_warning(
        run_id=ctx.run_id,
        expected_date=ctx.trade_date.isoformat(),
        latest_valid_date=detail.get("technical_date", ctx.trade_date.isoformat()),
        broker_date=detail.get("broker_date", "DATA_NOT_AVAILABLE"),
        fallback_used=False,
        broker_override=False,
        data_status=detail.get("status", detail.get("reason", "WAITING_DATA")),
        warnings=[
            "Final Watchlist belum dibuat karena broker summary belum valid.",
            f"Snapshot ID: {detail.get('snapshot_id') or 'DATA_NOT_AVAILABLE'}",
            f"Retry count: {detail.get('retry_count', detail.get('attempts', 0))}",
        ],
        config=ui_config(ctx),
    )
    return [ReportPayload("data_warning", "data_warning_final_watchlist.txt", text, topic="system")]


def preliminary_watchlist_payloads(ctx: RunnerContext, detail: dict[str, Any]) -> list[ReportPayload]:
    snapshot_path = resolve("data/output/snapshots") / ctx.trade_date.isoformat() / "latest_snapshot.json"
    snapshot = read_json(snapshot_path)
    candidate_path = Path(str(snapshot.get("output_paths", {}).get("technical_candidates", "")))
    candidates = load_csv(candidate_path)
    decisions = _technical_as_watch_decisions(candidates)
    text = format_watchlist(
        trade_date=ctx.trade_date,
        decisions=decisions,
        entry_plans=pd.DataFrame(),
        config=ui_config(ctx),
        preliminary=True,
    )
    text += f"\n\n📝 Broker readiness: {html_escape(detail.get('status', detail.get('reason', 'WAITING_DATA')))}"
    return [ReportPayload("preliminary_watchlist", "preliminary_watchlist.txt", text, topic="final_watchlist")]


def full_manual_payloads(ctx: RunnerContext, run_manifest: dict[str, Any] | None = None) -> list[ReportPayload]:
    """Full manual run sends the same compact final package as the 18:00 job.

    Pipeline success remains available in local status/log files; Telegram is not
    flooded with legacy recap, performance, and evaluation messages.
    """
    return final_watchlist_payloads(ctx, run_manifest or load_run_manifest(ctx))


def latest_run_manifest_for_job(ctx: RunnerContext) -> dict[str, Any]:
    manifest_dir = ctx.path("manifest_dir", "data/output/manifests")
    latest = latest_matching_file(manifest_dir, "SWING_RUN_MANIFEST_*.json")
    return read_json(latest) if latest else {}
