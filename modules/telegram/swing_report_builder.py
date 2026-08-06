#!/usr/bin/env python3
from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from modules.telegram.formatters import (
    exchange_warnings,
    escape_html,
    format_money as shared_format_money,
    format_number as shared_format_number,
    format_percent as shared_format_percent,
    format_price as shared_format_price,
    human_enum,
    human_status,
    risk_reward,
)

try:
    from modules.telegram.professional_ui import (
        UiConfig,
        broker_flow_summary as professional_broker_flow_summary,
        format_closing_bell as professional_closing_bell,
        format_data_warning as professional_data_warning,
        format_daily_signal_recap as professional_daily_signal_recap,
        format_exit_alert as professional_exit_alert,
        format_market_outlook as professional_market_outlook,
        format_pipeline_status as professional_pipeline_status,
        format_position_evaluation as professional_position_evaluation,
        format_signal_detail as professional_signal_detail,
        format_watchlist as professional_watchlist,
        rank_watchlist as professional_rank_watchlist,
    )
except ModuleNotFoundError:  # direct script execution via modules/telegram/telegram_bot.py
    from professional_ui import (
        UiConfig,
        broker_flow_summary as professional_broker_flow_summary,
        format_closing_bell as professional_closing_bell,
        format_data_warning as professional_data_warning,
        format_daily_signal_recap as professional_daily_signal_recap,
        format_exit_alert as professional_exit_alert,
        format_market_outlook as professional_market_outlook,
        format_pipeline_status as professional_pipeline_status,
        format_position_evaluation as professional_position_evaluation,
        format_signal_detail as professional_signal_detail,
        format_watchlist as professional_watchlist,
        rank_watchlist as professional_rank_watchlist,
    )


DECISION_ORDER = ["BUY READY", "BUY CANDIDATE", "WATCH", "AVOID"]
WATCHLIST_DECISIONS = {"BUY READY", "BUY CANDIDATE", "WATCH"}
EMOJI = {
    "success": "✅",
    "warning": "⚠️",
    "failed": "❌",
    "wait": "⏳",
    "info": "ℹ️",
    "market": "📊",
    "bull": "📈",
    "bear": "📉",
    "sideways": "➖",
    "ihsg": "🌐",
    "watchlist": "🎯",
    "strong_buy": "🚀",
    "buy": "🟢",
    "watch": "🟡",
    "speculative": "🟠",
    "avoid": "🔴",
    "new": "🆕",
    "continuing": "🔁",
    "removed": "🗑️",
    "broker": "🏦",
    "entry": "📍",
    "stop": "🛑",
    "tp": "🎯",
    "risk": "⚖️",
    "exit": "🚪",
    "database": "📚",
    "best": "🏆",
    "calendar": "🗓️",
}


@dataclass
class ReportMessage:
    message_type: str
    filename: str
    text: str
    attach_by_default: bool = False


def e(name: str, enabled: bool = True) -> str:
    return EMOJI.get(name, "") if enabled else ""


def norm_col(value: object) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def find_col(df: pd.DataFrame, *aliases: str) -> str | None:
    mapping = {norm_col(c): c for c in df.columns}
    for alias in aliases:
        key = norm_col(alias)
        if key in mapping:
            return mapping[key]
    return None


def load_csv(path: str | Path | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(p, low_memory=False)
    except Exception:
        return pd.DataFrame()


def load_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return {}
    try:
        import json
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def value(row: pd.Series | dict[str, Any], *aliases: str, default: Any = "Belum tersedia") -> Any:
    if isinstance(row, dict):
        for alias in aliases:
            if alias in row and row[alias] not in (None, ""):
                return row[alias]
        return default
    for alias in aliases:
        key = norm_col(alias)
        for col in row.index:
            if norm_col(col) == key:
                v = row[col]
                if pd.notna(v) and str(v).strip() != "":
                    return v
    return default


def to_float(v: Any) -> float | None:
    try:
        if v is None or str(v).strip() == "" or str(v).lower() == "nan":
            return None
        return float(str(v).replace(",", ""))
    except Exception:
        return None


def fmt_score(v: Any) -> str:
    return shared_format_number(v, 2, "Belum tersedia")


def fmt_pct(v: Any) -> str:
    return shared_format_percent(v, 2, signed=True, fallback="Belum tersedia")


def fmt_money(v: Any) -> str:
    return shared_format_price(v, "Belum tersedia")


def fmt_number(v: Any, decimals: int = 0) -> str:
    n = to_float(v)
    if n is None:
        return "Belum tersedia"
    return f"{n:,.{decimals}f}".replace(",", ".")


def fmt_bool(v: Any) -> str:
    text = str(v).strip().upper()
    return "Ya" if text in {"TRUE", "1", "YES", "YA"} else "Tidak"


def decision_counts(decisions: pd.DataFrame) -> dict[str, int]:
    if decisions.empty:
        return {d: 0 for d in DECISION_ORDER}
    col = find_col(decisions, "Decision_Status_Final", "Decision_Status", "Decision_V3", "Decision")
    if not col:
        return {d: 0 for d in DECISION_ORDER}
    values = decisions[col].astype(str).str.upper().str.strip().replace({
        "STRONG BUY": "BUY READY", "BUY": "BUY READY", "BUY ON TRIGGER": "BUY CANDIDATE",
        "WATCH HIGH": "WATCH", "SPECULATIVE": "WATCH",
    })
    counts = values.value_counts().to_dict()
    return {d: int(counts.get(d, 0)) for d in DECISION_ORDER}


def decision_icon(decision: str, use_emoji: bool) -> str:
    return {
        "BUY READY": e("strong_buy", use_emoji),
        "BUY CANDIDATE": e("buy", use_emoji),
        "STRONG BUY": e("strong_buy", use_emoji),
        "BUY": e("buy", use_emoji),
        "WATCH": e("watch", use_emoji),
        "SPECULATIVE": e("speculative", use_emoji),
        "AVOID": e("avoid", use_emoji),
    }.get(str(decision).upper(), "")


def simple_date(value_text: Any) -> str:
    parsed = pd.to_datetime(value_text, errors="coerce")
    if pd.isna(parsed):
        return str(value_text or "Belum tersedia")
    return parsed.strftime("%d %B %Y")


def section(title: str, lines: list[str], icon: str = "") -> list[str]:
    label = f"{icon} {title}".strip()
    return ["", f"<b>{escape_html(label)}</b>", *[escape_html(line) for line in lines]]


def build_pipeline_status(run_manifest: dict[str, Any], decisions: pd.DataFrame, exit_alerts: pd.DataFrame, use_emoji: bool) -> str:
    status = str(run_manifest.get("Pipeline_Status", "SUCCESS")).upper()
    icon = e("success", use_emoji) if status in {"SUCCESS", "COMPLETED", "OK"} else e("warning", use_emoji)
    counts = decision_counts(decisions)
    lines = [
        f"{icon} SDE SWING - PIPELINE SELESAI",
        "",
        "Run ID",
        escape_html(run_manifest.get("Run_ID", "Belum tersedia")),
        "",
        "Waktu",
        escape_html(simple_date(run_manifest.get("Finished_At") or datetime.now().isoformat())),
    ]
    lines += section("DATA", [
        f"Yahoo Refresh       : {run_manifest.get('Yahoo_Refresh_Status', 'Belum tersedia')}",
        f"Historical Terakhir : {run_manifest.get('Historical_Latest_Valid_Date', 'Belum tersedia')}",
        f"Technical Date      : {run_manifest.get('Technical_Date', 'Belum tersedia')}",
        f"Candidate Baru      : {fmt_bool(run_manifest.get('Candidate_Changed', False))}",
        f"Jumlah Candidate    : {run_manifest.get('Candidate_Count', len(decisions) if not decisions.empty else 0)}",
    ], e("database", use_emoji))
    lines += section("ZAPI ENRICHMENT", [
        f"Request            : {fmt_number(run_manifest.get('Zapi_Request_Count'), 0)}/{fmt_number(run_manifest.get('Zapi_Request_Cap', 5), 0)}",
        f"Metadata Cache     : {human_enum(run_manifest.get('Zapi_Metadata_Cache_Status', ''))}",
        f"Activity Cache     : {human_enum(run_manifest.get('Zapi_Market_Activity_Cache_Status', ''))}",
        f"Suspend / UMA      : {run_manifest.get('Suspended_Symbol_Count', 0)} / {run_manifest.get('Uma_Symbol_Count', 0)}",
        f"Relisting          : {run_manifest.get('Relisting_Symbol_Count', 0)}",
        f"Mode               : {'DEGRADED' if run_manifest.get('Zapi_Degraded') else 'NORMAL'}",
    ], e("warning", use_emoji))
    lines += section("BROKER", [
        f"Broker Date         : {run_manifest.get('Broker_Date', 'Belum tersedia')}",
        f"Coverage            : {run_manifest.get('Broker_Coverage', 'Belum tersedia')}",
        f"Date Override       : {fmt_bool(run_manifest.get('Broker_Date_Override', False))}",
    ], e("broker", use_emoji))
    lines += section("KEPUTUSAN", [
        f"{decision_icon('BUY READY', use_emoji)} BUY READY        : {counts['BUY READY']}",
        f"{decision_icon('BUY CANDIDATE', use_emoji)} BUY CANDIDATE   : {counts['BUY CANDIDATE']}",
        f"{decision_icon('WATCH', use_emoji)} WATCH            : {counts['WATCH']}",
        f"{decision_icon('AVOID', use_emoji)} AVOID            : {counts['AVOID']}",
        f"{e('exit', use_emoji)} Exit Alert       : {0 if exit_alerts.empty else len(exit_alerts)}",
    ], e("market", use_emoji))
    lines += ["", f"{icon} Kualitas Data: {run_manifest.get('Data_Quality_Status', 'VALID')}"]
    warnings = run_manifest.get("Warnings") or []
    if warnings:
        lines += ["", f"{e('warning', use_emoji)} <b>CATATAN DATA</b>", "\n".join(escape_html(human_enum(x)) for x in warnings)]
    return "\n".join(lines)


def build_market_recap(run_manifest: dict[str, Any], market_status: dict[str, Any], decisions: pd.DataFrame, use_emoji: bool) -> str:
    counts = decision_counts(decisions)
    regime = market_status.get("market_regime", "UNKNOWN")
    ihsg_close = market_status.get("ihsg_close")
    lines = [
        f"{e('market', use_emoji)} REKAP PASAR HARIAN - SWING",
        "",
        f"{e('calendar', use_emoji)} Tanggal",
        simple_date(run_manifest.get("Technical_Date") or market_status.get("date") or datetime.now().isoformat()),
    ]
    lines += section("KONDISI PASAR", [
        f"Market Regime : {regime}",
        f"IHSG Trend    : {regime}",
        f"IHSG Close    : {fmt_number(ihsg_close, 0)}",
        f"IHSG Change   : Belum tersedia",
        f"Market Risk   : {market_status.get('market_risk', 'Belum tersedia')}",
    ], e("ihsg", use_emoji))
    lines += section("HASIL PEMINDAIAN", [
        f"Saham Diproses     : {len(decisions) if not decisions.empty else 'Belum tersedia'}",
        f"Kandidat Teknikal  : {run_manifest.get('Candidate_Count', 'Belum tersedia')}",
        f"Broker Confirm     : {broker_confirm_count(decisions)}",
        f"{decision_icon('BUY READY', use_emoji)} BUY READY       : {counts['BUY READY']}",
        f"{decision_icon('BUY CANDIDATE', use_emoji)} BUY CANDIDATE  : {counts['BUY CANDIDATE']}",
        f"{decision_icon('WATCH', use_emoji)} WATCH           : {counts['WATCH']}",
        f"{decision_icon('AVOID', use_emoji)} AVOID           : {counts['AVOID']}",
    ], "🔎" if use_emoji else "")
    lines += section("RINGKASAN", ["Data pembanding run sebelumnya belum tersedia."], "💡" if use_emoji else "")
    return "\n".join(lines)


def broker_confirm_count(decisions: pd.DataFrame) -> int:
    if decisions.empty:
        return 0
    col = find_col(decisions, "Broker_Confirmation")
    if not col:
        return 0
    return int(decisions[col].astype(str).str.upper().isin({"ACCUMULATION", "STRONG ACCUMULATION"}).sum())


def current_watchlist(decisions: pd.DataFrame) -> pd.DataFrame:
    if decisions.empty:
        return pd.DataFrame()
    decision_col = find_col(decisions, "Decision_Status_Final", "Decision_Status", "Decision_V3", "Decision")
    symbol_col = find_col(decisions, "Symbol", "EMITEN", "Ticker")
    if not decision_col or not symbol_col:
        return pd.DataFrame()
    out = decisions.copy()
    out["_Decision"] = out[decision_col].astype(str).str.upper().str.strip().replace({"STRONG BUY": "BUY READY", "BUY": "BUY READY", "BUY ON TRIGGER": "BUY CANDIDATE", "WATCH HIGH": "WATCH", "SPECULATIVE": "WATCH"})
    out["_Symbol"] = out[symbol_col].astype(str).str.upper().str.strip().str.replace(".JK", "", regex=False)
    return out[out["_Decision"].isin(WATCHLIST_DECISIONS)].copy()


def sort_by_score(df: pd.DataFrame) -> pd.DataFrame:
    score_col = find_col(df, "Final_Score_V3", "Final_Score", "Technical_Score")
    if score_col:
        work = df.copy()
        work["_score"] = pd.to_numeric(work[score_col], errors="coerce").fillna(-1)
        return work.sort_values("_score", ascending=False).drop(columns=["_score"])
    return df


def stock_block(row: pd.Series, entry_plans: pd.DataFrame, use_emoji: bool) -> str:
    symbol = value(row, "Symbol", "EMITEN", "Ticker", default="?")
    decision = str(value(row, "Decision_Status_Final", "Decision_Status", "Decision_V3", "Decision", default="WATCH")).upper()
    plan = pd.Series(dtype=object)
    if not entry_plans.empty:
        sym_col = find_col(entry_plans, "Symbol")
        if sym_col:
            found = entry_plans[entry_plans[sym_col].astype(str).str.upper().str.replace(".JK", "", regex=False) == str(symbol).upper()]
            if not found.empty:
                candidate_plan = found.iloc[0]
                plan = candidate_plan
                decision = str(value(candidate_plan, "Decision_Status_Final", default=decision)).upper()
    public_decision = human_status(decision)
    rr_text, rr_valid = ("R:R belum valid", False)
    if not plan.empty:
        rr_text, rr_valid = risk_reward(
            value(plan, "Entry_Zone_Low", "Entry_Low"),
            value(plan, "Entry_Zone_High", "Entry_High"),
            value(plan, "Target_1", "Take_Profit_1"),
            value(plan, "Initial_Stop", "Stop_Loss"),
            entry_reference=value(plan, "Entry_Reference", "Entry_Price"),
        )
        if public_decision == "BUY READY" and not rr_valid:
            public_decision = "WAITING"
    exchange_status = str(value(row, "Exchange_Status", default="NORMAL")).upper()
    risk_flags = value(row, "Risk_Flags", default="")
    exchange_veto = value(row, "Exchange_Veto", "Veto", "Veto_Reason", default="")
    exchange_warning_lines = exchange_warnings(exchange_status, risk_flags, exchange_veto)
    lines = [
        f"{decision_icon(public_decision, use_emoji)} <b>{escape_html(str(symbol).upper())} — {escape_html(public_decision)}</b>",
        f"Final Score  : {fmt_score(value(row, 'Final_Score_V3', 'Final_Score'))}",
        f"Technical    : {fmt_score(value(row, 'Technical_Score_Final', 'Technical_Score'))}",
        f"Broker       : {fmt_score(value(row, 'Broker_Score'))}",
        f"Confirmation : {escape_html(human_enum(value(row, 'Broker_Confirmation', default='')))}",
        f"{e('entry', use_emoji)} Entry     : {entry_text(plan)}",
        f"{e('stop', use_emoji)} Stop Loss : {fmt_money(value(plan, 'Initial_Stop', 'Stop_Loss')) if not plan.empty else 'Belum tersedia'}",
        f"{e('tp', use_emoji)} TP1       : {fmt_money(value(plan, 'Target_1', 'Take_Profit_1')) if not plan.empty else 'Belum tersedia'}",
        f"{e('tp', use_emoji)} TP2       : {fmt_money(value(plan, 'Target_2', 'Take_Profit_2')) if not plan.empty else 'Belum tersedia'}",
        f"R:R TP1    : {escape_html(rr_text)}",
        f"Bursa       : {escape_html(exchange_status)}",
        f"💧 Liquidity : {escape_html(human_enum(value(row, 'Liquidity_Class', default='Belum tersedia')))}",
    ]
    lines.extend(f"⚠️ Bursa     : {escape_html(item)}" for item in exchange_warning_lines)
    reason = value(row, "Rejected_By", "Decision_Reasons", "Candidate_Reason", default="")
    if reason:
        lines.append(f"Reason    : {escape_html(human_enum(reason))}")
    return "\n".join(lines)


def entry_text(plan: pd.Series) -> str:
    if plan.empty:
        return "Belum tersedia"
    low = value(plan, "Entry_Zone_Low", "Entry_Low", default="")
    high = value(plan, "Entry_Zone_High", "Entry_High", default="")
    if low != "" and high != "":
        return f"{fmt_money(low)}-{fmt_money(high)}"
    ref = value(plan, "Reference_Close", "Entry_Price", default="")
    return fmt_money(ref) if ref != "" else "Belum tersedia"


def chunk_blocks(title: str, intro: list[str], blocks: list[str], limit: int, use_emoji: bool) -> list[str]:
    if not blocks:
        return ["\n".join([title, *intro, "", "Data tidak tersedia"])]
    chunks: list[list[str]] = []
    current = [title, *intro]
    for block in blocks:
        candidate = "\n\n".join([*current, block])
        if current != [title, *intro] and len(candidate) > limit:
            chunks.append(current)
            current = [title, *intro, block]
        else:
            current.append(block)
    chunks.append(current)
    if len(chunks) == 1:
        return ["\n\n".join(chunks[0])]
    out = []
    for idx, lines in enumerate(chunks, 1):
        header = f"{title}\nBagian {idx}/{len(chunks)}"
        tail = lines[1:] if lines and lines[0] == title else lines
        out.append("\n\n".join([header, *tail]))
    return out


def build_watchlist_recap(decisions: pd.DataFrame, entry_plans: pd.DataFrame, max_length: int, use_emoji: bool) -> list[ReportMessage]:
    watch = sort_by_score(current_watchlist(decisions))
    counts = decision_counts(decisions)
    total = len(watch)
    score_col = find_col(watch, "Final_Score_V3", "Final_Score")
    highest = "Belum tersedia"
    if not watch.empty:
        top = watch.iloc[0]
        highest = f"{value(top, 'Symbol', 'EMITEN', 'Ticker')} - Final Score {fmt_score(value(top, 'Final_Score_V3', 'Final_Score'))}"
    intro = [
        "",
        f"{e('calendar', use_emoji)} Tanggal",
        simple_date(datetime.now().isoformat()),
        "",
        "RINGKASAN",
        f"Total Watchlist : {total}",
        f"{e('new', use_emoji)} New          : {total}",
        f"{e('continuing', use_emoji)} Continuing   : 0",
        f"{e('removed', use_emoji)} Removed      : 0",
        f"{decision_icon('BUY READY', use_emoji)} BUY READY      : {counts['BUY READY']}",
        f"{decision_icon('BUY CANDIDATE', use_emoji)} BUY CANDIDATE : {counts['BUY CANDIDATE']}",
        f"{decision_icon('WATCH', use_emoji)} WATCH          : {counts['WATCH']}",
        "",
        f"{e('best', use_emoji)} HIGHEST SCORE",
        highest,
    ]
    blocks = [stock_block(row, entry_plans, use_emoji) for _, row in watch.iterrows()]
    title = f"{e('watchlist', use_emoji)} REKAP WATCHLIST SWING".strip()
    chunks = chunk_blocks(title, intro, blocks, max_length, use_emoji)
    messages = []
    for idx, text in enumerate(chunks, 1):
        suffix = f"_{idx:02d}" if len(chunks) > 1 else ""
        messages.append(ReportMessage("watchlist_recap", f"03_watchlist_recap{suffix}.txt", text, attach_by_default=len(text) > max_length * 0.75))
    return messages


def build_performance_recap(outcomes: pd.DataFrame, summary: pd.DataFrame, use_emoji: bool) -> str:
    lines = [
        f"{e('best', use_emoji)} PERFORMA WATCHLIST SWING",
        "",
        f"{e('calendar', use_emoji)} Periode",
        "Belum tersedia",
    ]
    if outcomes.empty:
        lines += section("HASIL", ["Data tidak tersedia"], e("market", use_emoji))
        return "\n".join(lines)
    outcome_col = find_col(outcomes, "Final_Outcome_D7", "final_outcome")
    ret_col = find_col(outcomes, "Return_D7_Pct", "return_d7")
    mfe_col = find_col(outcomes, "MFE_D7_Pct", "mfe")
    mae_col = find_col(outcomes, "MAE_D7_Pct", "mae")
    valid = outcomes[outcomes[outcome_col].astype(str).ne("OPEN")] if outcome_col else outcomes
    wins = int(valid[outcome_col].astype(str).str.upper().eq("WIN").sum()) if outcome_col and not valid.empty else 0
    losses = int(valid[outcome_col].astype(str).str.upper().eq("LOSS").sum()) if outcome_col and not valid.empty else 0
    win_rate = wins / len(valid) * 100 if len(valid) else math.nan
    returns = pd.to_numeric(outcomes[ret_col], errors="coerce") if ret_col else pd.Series(dtype=float)
    best = "Belum tersedia"
    worst = "Belum tersedia"
    if ret_col and returns.dropna().any():
        best_row = outcomes.loc[returns.idxmax()]
        worst_row = outcomes.loc[returns.idxmin()]
        best = f"{value(best_row, 'Symbol')} - {fmt_pct(best_row[ret_col])}"
        worst = f"{value(worst_row, 'Symbol')} - {fmt_pct(worst_row[ret_col])}"
    lines += section("HASIL", [
        f"Total Signal : {len(outcomes)}",
        f"Valid Signal : {len(valid)}",
        f"Closed       : {len(valid)}",
        f"Open         : {len(outcomes) - len(valid)}",
        f"Win          : {wins}",
        f"Loss         : {losses}",
        f"Ambiguous    : {int(valid[outcome_col].astype(str).str.upper().eq('AMBIGUOUS').sum()) if outcome_col and not valid.empty else 0}",
        f"{e('tp', use_emoji)} Win Rate  : {fmt_pct(win_rate) if not math.isnan(win_rate) else 'Belum tersedia'}",
    ], e("market", use_emoji))
    lines += section("RETURN DAN RISIKO", [
        f"Average Return : {fmt_pct(returns.mean()) if returns.dropna().any() else 'Belum tersedia'}",
        f"Average MFE    : {fmt_pct(pd.to_numeric(outcomes[mfe_col], errors='coerce').mean()) if mfe_col else 'Belum tersedia'}",
        f"Average MAE    : {fmt_pct(pd.to_numeric(outcomes[mae_col], errors='coerce').mean()) if mae_col else 'Belum tersedia'}",
        f"TP1 Hit Rate   : {fmt_pct(pd.to_numeric(outcomes[find_col(outcomes, 'TP1_Hit_D7', 'tp1_hit')], errors='coerce').mean() * 100) if find_col(outcomes, 'TP1_Hit_D7', 'tp1_hit') else 'Belum tersedia'}",
        f"TP2 Hit Rate   : {fmt_pct(pd.to_numeric(outcomes[find_col(outcomes, 'TP2_Hit_D7', 'tp2_hit')], errors='coerce').mean() * 100) if find_col(outcomes, 'TP2_Hit_D7', 'tp2_hit') else 'Belum tersedia'}",
        f"SL Hit Rate    : {fmt_pct(pd.to_numeric(outcomes[find_col(outcomes, 'SL_Hit_D7', 'sl_hit')], errors='coerce').mean() * 100) if find_col(outcomes, 'SL_Hit_D7', 'sl_hit') else 'Belum tersedia'}",
    ], e("bull", use_emoji))
    lines += ["", f"{e('best', use_emoji)} Best Performer", escape_html(best), "", f"{e('bear', use_emoji)} Worst Performer", escape_html(worst)]
    return "\n".join(lines)


def build_exit_alerts(alerts: pd.DataFrame, use_emoji: bool) -> list[ReportMessage]:
    if alerts.empty:
        return [ReportMessage("exit_alert", "05_exit_alert.txt", f"{e('exit', use_emoji)} EXIT ALERT - SWING\n\nData tidak tersedia")]
    messages = []
    for idx, (_, row) in enumerate(alerts.iterrows(), 1):
        symbol = value(row, "Symbol", default="?")
        exit_status = human_status(value(row, "Alert", "Exit_Status", default="EXIT"))
        exit_reason = human_enum(value(row, "Reason", "Exit_Reason", default="data tidak tersedia"))
        text = "\n".join([
            f"{e('exit', use_emoji)} EXIT ALERT - SWING",
            "",
            f"{e('avoid', use_emoji)} {escape_html(symbol)}",
            f"Exit Status : {escape_html(exit_status)}",
            f"Exit Reason : {escape_html(exit_reason)}",
            f"Entry       : {fmt_money(value(row, 'Entry_Price'))}",
            f"Exit Reference : {fmt_money(value(row, 'Exit_Price'))}",
            f"Return      : {fmt_pct(value(row, 'Return_Pct'))}",
            f"Holding     : {escape_html(shared_format_number(value(row, 'Holding_Days', default='Belum tersedia'), 0))} trading days",
            "",
            f"{e('warning', use_emoji)} Periksa kondisi pasar sebelum melakukan tindakan.",
        ])
        messages.append(ReportMessage("exit_alert", f"05_exit_alert_{idx:02d}.txt", text))
    return messages


def build_warning_messages(run_manifest: dict[str, Any], use_emoji: bool) -> list[ReportMessage]:
    warnings = run_manifest.get("Warnings") or []
    quality = str(run_manifest.get("Data_Quality_Status", "VALID"))
    if quality == "VALID" and not warnings and not run_manifest.get("Fallback_Used") and not run_manifest.get("Broker_Date_Override"):
        return []
    lines = [
        f"{e('warning', use_emoji)} <b>SDE SWING — DATA WARNING</b>",
        "",
        f"Run ID        : {escape_html(run_manifest.get('Run_ID', 'Belum tersedia'))}",
        f"Expected Date : {escape_html(run_manifest.get('Latest_Expected_Trading_Date', run_manifest.get('Technical_Date', 'Belum tersedia')))}",
        f"Latest Valid  : {escape_html(run_manifest.get('Historical_Latest_Valid_Date', 'Belum tersedia'))}",
        f"Broker Date   : {escape_html(run_manifest.get('Broker_Date', 'Belum tersedia'))}",
        f"Fallback Used : {fmt_bool(run_manifest.get('Fallback_Used', False))}",
        f"Broker Override : {fmt_bool(run_manifest.get('Broker_Date_Override', False))}",
        "",
        f"{e('warning', use_emoji)} Status:",
        escape_html(human_enum(quality)),
    ]
    if warnings:
        lines += ["", "<b>Catatan:</b>", *[escape_html(human_enum(x)) for x in warnings]]
    return [ReportMessage("warning", "06_warning.txt", "\n".join(lines), attach_by_default=True)]


def _strip_emoji(text: str) -> str:
    emoji_pattern = re.compile(
        "["
        "\\U0001F1E0-\\U0001F1FF"
        "\\U0001F300-\\U0001FAFF"
        "\\U00002600-\\U000027BF"
        "\\U0000FE0F"
        "]+",
        flags=re.UNICODE,
    )
    cleaned = emoji_pattern.sub("", text)
    return "\n".join(line.rstrip() for line in cleaned.splitlines())


def _latest_global_snapshot() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2] / "data/output/global_market"
    candidates = sorted(root.glob("*/global_market_snapshot.json"), key=lambda path: path.stat().st_mtime if path.exists() else 0)
    return load_json(candidates[-1]) if candidates else {}


def build_all_reports(
    run_manifest: dict[str, Any],
    decisions: pd.DataFrame,
    entry_plans: pd.DataFrame,
    market_status: dict[str, Any],
    exit_alerts: pd.DataFrame,
    outcomes: pd.DataFrame,
    backtest_summary: pd.DataFrame,
    max_length: int = 3800,
    use_emoji: bool = True,
) -> list[ReportMessage]:
    """Build all Swing Telegram reports through one centralized UI layer."""
    cfg = UiConfig(max_message_length=max_length, max_watchlist_items=5)
    root = Path(__file__).resolve().parents[2]
    technical = load_csv(root / "data/output/technical/latest_technical_features.csv")
    ihsg = load_csv(root / "data/input/IHSG.csv")
    active = load_csv(root / "data/state/ACTIVE_TRADES.csv")
    global_snapshot = _latest_global_snapshot()
    trade_date = run_manifest.get("Technical_Date") or market_status.get("date") or datetime.now().date().isoformat()

    def finish(text: str) -> str:
        if use_emoji:
            return text
        return _strip_emoji(text).replace("data tidak tersedia", "Belum tersedia")

    messages: list[ReportMessage] = [
        ReportMessage(
            "pipeline_status",
            "01_pipeline_status.txt",
            finish(professional_pipeline_status(run_manifest, decisions, exit_alerts, cfg, entry_plans=entry_plans)),
        ),
    ]

    regime = str(market_status.get("market_regime", "UNKNOWN")).upper()
    if regime in {"BEAR", "BEARISH"}:
        plan_summary = "Bersikap defensif dan hanya fokus pada kandidat terbaik dengan konfirmasi lengkap."
    elif regime in {"BULL", "BULLISH"}:
        plan_summary = "Cari entry secara selektif pada breakout valid atau pullback sehat dengan volume mendukung."
    else:
        plan_summary = "Market masih selektif. Tunggu valid entry dan jangan mengejar harga."
    messages.append(ReportMessage(
        "market_outlook",
        "02_market_outlook.txt",
        finish(professional_market_outlook(
            trade_date=trade_date,
            market_status=market_status,
            global_snapshot=global_snapshot,
            broker_flow=professional_broker_flow_summary(decisions),
            plan_summary=plan_summary,
            focus=[
                "Trend, momentum, dan volume yang selaras.",
                "Harga dekat area entry dengan risk reward layak.",
                "Broker flow mendukung dan data tetap valid.",
            ],
            risks=[
                "False breakout dan harga terlalu jauh dari entry.",
                "Distribusi broker atau perubahan regime market.",
            ],
            config=cfg,
        )),
    ))
    messages.append(ReportMessage(
        "daily_signal_recap",
        "03_daily_signal_recap.txt",
        finish(professional_daily_signal_recap(trade_date, decisions, market_status, technical, config=cfg, entry_plans=entry_plans)),
    ))
    messages.append(ReportMessage(
        "closing_bell",
        "04_closing_bell.txt",
        finish(professional_closing_bell(trade_date, ihsg, market_status, global_snapshot, decisions, entry_plans, config=cfg)),
    ))
    messages.append(ReportMessage(
        "watchlist_recap",
        "05_watchlist_recap.txt",
        finish(professional_watchlist(trade_date, decisions, entry_plans, config=cfg)),
        attach_by_default=False,
    ))

    ranked = professional_rank_watchlist(decisions, entry_plans).head(cfg.max_watchlist_items)
    symbol_col = find_col(ranked, "Symbol", "EMITEN", "Ticker")
    for idx, (_, row) in enumerate(ranked.iterrows(), 1):
        symbol = str(value(row, symbol_col or "Symbol", default="?")).upper()
        plan = pd.Series(dtype=object)
        if not entry_plans.empty:
            plan_symbol_col = find_col(entry_plans, "Symbol", "EMITEN")
            if plan_symbol_col:
                found = entry_plans[entry_plans[plan_symbol_col].astype(str).str.upper() == symbol]
                if not found.empty:
                    plan = found.iloc[0]
        messages.append(ReportMessage(
            "signal_detail",
            f"06_signal_detail_{idx:02d}_{symbol}.txt",
            finish(professional_signal_detail(trade_date, row, plan, config=cfg)),
        ))

    messages.append(ReportMessage(
        "position_evaluation",
        "07_position_evaluation.txt",
        finish(professional_position_evaluation(trade_date, active, exit_alerts, decisions, entry_plans, config=cfg)),
    ))

    quality = str(run_manifest.get("Data_Quality_Status", "VALID")).upper()
    warnings = [str(item) for item in run_manifest.get("Warnings", []) if str(item).strip()]
    fallback = bool(run_manifest.get("Fallback_Used", False))
    broker_override = bool(run_manifest.get("Broker_Date_Override", False))
    if quality != "VALID" or warnings or fallback or broker_override:
        messages.append(ReportMessage(
            "warning",
            "08_data_warning.txt",
            finish(professional_data_warning(
                run_id=run_manifest.get("Run_ID", "Belum tersedia"),
                expected_date=run_manifest.get("Latest_Expected_Trading_Date", trade_date),
                latest_valid_date=run_manifest.get("Historical_Latest_Valid_Date", trade_date),
                broker_date=run_manifest.get("Broker_Date", "DATA_NOT_AVAILABLE"),
                fallback_used=fallback,
                broker_override=broker_override,
                data_status=quality,
                warnings=warnings,
                data_source=str(run_manifest.get("Data_Source", "")),
                config=cfg,
            )),
            attach_by_default=True,
        ))

    for idx, (_, row) in enumerate(exit_alerts.iterrows(), 1):
        messages.append(ReportMessage(
            "exit_alert",
            f"09_exit_alert_{idx:02d}.txt",
            finish(professional_exit_alert(row, config=cfg)),
        ))
    return messages


def write_messages(messages: list[ReportMessage], folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for message in messages:
        path = folder / message.filename
        path.write_text(message.text, encoding="utf-8")
        paths.append(path)
    return paths

