#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
try:
    import requests
except ImportError:
    requests = None

from swing_report_builder import (
    build_all_reports,
    load_csv as load_report_csv,
    load_json as load_report_json,
    write_messages,
)
from modules.telegram.formatters import (
    exchange_warnings,
    format_number,
    format_percent,
    format_price,
    human_enum,
    human_status,
    risk_reward,
)


def norm_col(value: str) -> str:
    value = str(value).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_")


def find_col(df: pd.DataFrame, *aliases: str) -> str | None:
    mapping = {norm_col(c): c for c in df.columns}
    for alias in aliases:
        if norm_col(alias) in mapping:
            return mapping[norm_col(alias)]
    return None


def value(row: pd.Series, *aliases: str, default: Any = "") -> Any:
    for alias in aliases:
        for col in row.index:
            if norm_col(col) == norm_col(alias):
                v = row[col]
                if pd.notna(v):
                    return v
    return default


def money(v: Any) -> str:
    return format_price(v)


def pct(v: Any) -> str:
    return format_percent(v, 2, signed=True)


def esc(text: Any) -> str:
    # Telegram HTML parse mode.
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class TelegramClient:
    def __init__(self, token: str, chat_id: str, dry_run: bool = False, timeout: int = 30, parse_mode: str = "HTML"):
        self.token = token
        self.chat_id = chat_id
        self.dry_run = dry_run
        self.timeout = timeout
        # Reports are escaped for Telegram HTML throughout the runtime.
        self.parse_mode = "HTML"
        self.base_url = f"https://api.telegram.org/bot{token}" if token else ""

    def _post(self, method: str, data=None, files=None) -> dict:
        if self.dry_run:
            print(f"[DRY RUN] {method}")
            if data:
                print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
            if files:
                print(f"files={list(files)}")
            return {"ok": True, "result": {"dry_run": True}}
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN belum diisi.")
        if not self.chat_id:
            raise RuntimeError("TELEGRAM_CHAT_ID belum diisi.")
        if requests is None:
            raise RuntimeError("Dependency requests belum terpasang. Jalankan: pip install -r requirements.txt")
        response = requests.post(
            f"{self.base_url}/{method}",
            data=data,
            files=files,
            timeout=self.timeout,
        )
        try:
            payload = response.json()
        except Exception as exc:
            raise RuntimeError(f"Respons Telegram bukan JSON: HTTP {response.status_code}") from exc
        if not response.ok or not payload.get("ok"):
            raise RuntimeError(f"Telegram API gagal: {payload}")
        return payload

    def send_message(self, text: str, disable_preview: bool = True) -> dict:
        return self._post("sendMessage", data={
            "chat_id": self.chat_id,
            "text": text[:4096],
            "parse_mode": self.parse_mode,
            "disable_web_page_preview": str(disable_preview).lower(),
        })

    def send_document(self, path: Path, caption: str = "") -> dict:
        if not path.exists():
            raise FileNotFoundError(path)
        if self.dry_run:
            return self._post("sendDocument", data={
                "chat_id": self.chat_id, "caption": caption
            }, files={"document": path.name})
        with path.open("rb") as fh:
            return self._post("sendDocument", data={
                "chat_id": self.chat_id,
                "caption": caption[:1024],
                "parse_mode": self.parse_mode,
            }, files={"document": (path.name, fh)})


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_market_regime(path: str | Path | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        regime = str(payload.get("market_regime", "")).strip().upper()
        return regime if regime in {"BULL", "SIDEWAYS", "BEAR", "UNKNOWN"} else None
    except Exception:
        return None


def read_csv_optional(path: str | Path | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(p, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def decision_subset(df: pd.DataFrame, name: str) -> pd.DataFrame:
    decision_col = find_col(df, "Decision_V3", "Gated_Decision", "Decision")
    if not decision_col:
        return pd.DataFrame()
    return df[df[decision_col].astype(str).str.upper().str.strip() == name].copy()


def sort_signals(df: pd.DataFrame) -> pd.DataFrame:
    score_col = find_col(df, "Final_Score_V3", "Final_Score", "Signal_Score", "Score")
    if score_col:
        return df.sort_values(score_col, ascending=False)
    return df


def signal_block(row: pd.Series, index: int, entry_plans: pd.DataFrame) -> str:
    symbol = str(value(row, "Symbol", "Ticker", "EMITEN", default="?")).upper()
    score = value(row, "Final_Score_V3", "Final_Score", "Score", default="-")
    broker = human_enum(value(row, "Broker_Confirmation", "Broker_Status", default=""))
    liquidity = human_enum(value(row, "Liquidity_Class", default=""))
    exchange_status = str(value(row, "Exchange_Status", default="NORMAL") or "NORMAL").upper()
    exchange_risk = value(row, "Risk_Flags", default="")
    exchange_veto = value(row, "Exchange_Veto", "Veto", "Veto_Reason", default="")
    exchange_warning_lines = exchange_warnings(exchange_status, exchange_risk, exchange_veto)

    plan = pd.Series(dtype=object)
    if not entry_plans.empty:
        symbol_col = find_col(entry_plans, "Symbol")
        if symbol_col:
            found = entry_plans[entry_plans[symbol_col].astype(str).str.upper() == symbol]
            if not found.empty:
                plan = found.iloc[0]

    close_value = value(row, "Close", "Current_Price", "CURRENT_PRICE", default="-")
    technical_date = value(row, "Technical_Data_Date", "Date", default="-")
    broker_date = value(row, "Broker_Data_Date", "TO_DATE_BROKER", "TO_DATE", default="-")
    lines = [
        f"<b>{index}. {esc(symbol)}</b>",
        f"Status: <b>{esc(human_status(value(row, 'Decision_V3', 'Decision', default='WATCH')))}</b>",
        f"Score: <b>{esc(format_percent(score, 1))}</b>",
        f"Close: <b>{money(close_value)}</b>",
        f"Technical date: {esc(technical_date)}",
        f"Broker date: {esc(broker_date)}",
        f"Broker: {esc(broker)}",
        f"Liquidity: {esc(liquidity)}",
        *[f"⚠️ Bursa: {esc(item)}" for item in exchange_warning_lines],
    ]
    if not plan.empty:
        status = value(plan, "Plan_Status", default="")
        if status:
            lines.append(f"Plan: <b>{esc(human_status(status))}</b>")
        if str(status).upper() == "ACCEPT":
            rr_text, _rr_valid = risk_reward(
                value(plan, "Entry_Zone_Low"), value(plan, "Entry_Zone_High"),
                value(plan, "Target_1"), value(plan, "Initial_Stop"),
                entry_reference=value(plan, "Entry_Reference"),
            )
            lines.extend([
                f"Entry: {money(value(plan, 'Entry_Zone_Low'))}–{money(value(plan, 'Entry_Zone_High'))}",
                f"Stop: {money(value(plan, 'Initial_Stop'))}",
                f"Target 1: {money(value(plan, 'Target_1'))}",
                f"Target 2: {money(value(plan, 'Target_2'))}",
                f"R:R TP1: <b>{esc(rr_text)}</b>",
            ])
        elif value(plan, "Rejection_Reason", default=""):
            lines.append(f"Alasan: {esc(value(plan, 'Rejection_Reason'))}")
    return "\n".join(lines)


def detect_regime(decisions: pd.DataFrame, explicit: str | None = None) -> str:
    if explicit:
        return explicit.upper()
    col = find_col(decisions, "Market_Regime")
    if col and not decisions[col].dropna().empty:
        return str(decisions[col].dropna().mode().iloc[0]).upper()
    return "UNKNOWN"


def max_date_text(df: pd.DataFrame, *aliases: str) -> str:
    col = find_col(df, *aliases)
    if not col:
        return "UNKNOWN"
    parsed = pd.to_datetime(df[col], errors="coerce")
    if parsed.dropna().empty:
        return "UNKNOWN"
    return parsed.max().strftime("%Y-%m-%d")


def freshness_summary(decisions: pd.DataFrame) -> tuple[str, str, int | None]:
    technical_date = max_date_text(decisions, "Technical_Data_Date", "Date")
    broker_date = max_date_text(decisions, "Broker_Data_Date", "TO_DATE_BROKER", "TO_DATE")
    age = None
    try:
        td = pd.Timestamp(technical_date)
        bd = pd.Timestamp(broker_date)
        age = max(0, len(pd.bdate_range(bd, td)) - 1)
    except Exception:
        pass
    return technical_date, broker_date, age


def daily_message(
    decisions: pd.DataFrame,
    entry_plans: pd.DataFrame,
    pipeline_status: str,
    regime: str,
    report_date: str,
    top_watch: int,
) -> str:
    strong = sort_signals(decision_subset(decisions, "STRONG BUY"))
    buy = sort_signals(decision_subset(decisions, "BUY"))
    watch = sort_signals(decision_subset(decisions, "WATCH")).head(top_watch)
    speculative = decision_subset(decisions, "SPECULATIVE")
    avoid = decision_subset(decisions, "AVOID")

    icon = "✅" if pipeline_status.upper() == "SUCCESS" else "⚠️"
    technical_date, broker_date, broker_age = freshness_summary(decisions)
    lines = [
        f"<b>SDE DAILY SIGNAL</b>",
        esc(report_date),
        "",
        f"{icon} Pipeline: <b>{esc(pipeline_status.upper())}</b>",
        f"Market regime: <b>{esc(regime)}</b>",
        f"Technical data: <b>{esc(technical_date)}</b>",
        f"Broker data: <b>{esc(broker_date)}</b>",
    ]
    if broker_age is not None and broker_age > 0:
        lines.append(f"⚠️ Broker snapshot tertinggal <b>{broker_age} hari bursa</b>; keputusan dapat tetap mirip.")
    lines.extend([
        "",
        f"<b>BUY READY ({len(strong)})</b>",
    ])
    if strong.empty:
        lines.append("Tidak ada.")
    else:
        for i, (_, row) in enumerate(strong.iterrows(), 1):
            lines.extend([signal_block(row, i, entry_plans), ""])

    lines.append(f"<b>BUY CANDIDATE ({len(buy)})</b>")
    if buy.empty:
        lines.append("Tidak ada.")
    else:
        for i, (_, row) in enumerate(buy.iterrows(), 1):
            lines.extend([signal_block(row, i, entry_plans), ""])

    lines.append(f"<b>TOP WATCH ({len(watch)})</b>")
    if watch.empty:
        lines.append("Tidak ada.")
    else:
        symbol_col = find_col(watch, "Symbol", "Ticker", "EMITEN")
        score_col = find_col(watch, "Final_Score_V3", "Final_Score", "Score")
        for i, (_, row) in enumerate(watch.iterrows(), 1):
            symbol = row[symbol_col] if symbol_col else "?"
            score = format_number(row[score_col], 1) if score_col else "data tidak tersedia"
            lines.append(f"{i}. <b>{esc(symbol)}</b> — score {esc(score)}")

    lines.extend([
        "",
        f"SPECULATIVE: <b>{len(speculative)}</b>",
        f"AVOID: <b>{len(avoid)}</b>",
    ])
    return "\n".join(lines)


def exit_messages(alerts: pd.DataFrame) -> list[str]:
    messages = []
    for _, row in alerts.iterrows():
        symbol = value(row, "Symbol", default="?")
        reason = value(row, "Reason", "Exit_Reason", default="-")
        messages.append("\n".join([
            "🚨 <b>SDE EXIT ALERT</b>",
            "",
            f"Symbol: <b>{esc(symbol)}</b>",
            f"Entry: {money(value(row, 'Entry_Price', default='-'))}",
            f"Exit: {money(value(row, 'Exit_Price', default='-'))}",
            f"Return: <b>{pct(value(row, 'Return_Pct', default='-'))}</b>",
            f"Holding: {esc(value(row, 'Holding_Days', default='-'))} hari",
            f"Reason: {esc(human_enum(reason))}",
        ]))
    return messages


def weekly_message(summary: pd.DataFrame, regime_summary: pd.DataFrame, report_date: str) -> str:
    lines = ["<b>SDE WEEKLY REVIEW</b>", esc(report_date), ""]
    if summary.empty:
        lines.append("Backtest summary belum tersedia.")
        return "\n".join(lines)

    decision_col = find_col(summary, "Decision")
    all_row = summary[summary[decision_col].astype(str).str.upper() == "ALL"] if decision_col else pd.DataFrame()
    row = all_row.iloc[0] if not all_row.empty else summary.iloc[0]
    lines.extend([
        f"Signals: <b>{esc(value(row, 'Signals', default='-'))}</b>",
        f"5D win rate: <b>{pct(value(row, 'Win_Rate_5D_Pct', default='-'))}</b>",
        f"5D average: <b>{pct(value(row, 'Avg_Return_5D_Pct', default='-'))}</b>",
        f"10D win rate: <b>{pct(value(row, 'Win_Rate_10D_Pct', default='-'))}</b>",
        f"10D average: <b>{pct(value(row, 'Avg_Return_10D_Pct', default='-'))}</b>",
        f"20D win rate: <b>{pct(value(row, 'Win_Rate_20D_Pct', default='-'))}</b>",
        f"20D average: <b>{pct(value(row, 'Avg_Return_20D_Pct', default='-'))}</b>",
    ])

    if not regime_summary.empty:
        lines.extend(["", "<b>Market regime sample</b>"])
        reg_col = find_col(regime_summary, "Market_Regime")
        sig_col = find_col(regime_summary, "Signals")
        if reg_col:
            grouped = regime_summary.groupby(reg_col)[sig_col].sum() if sig_col else regime_summary.groupby(reg_col).size()
            for regime, count in grouped.items():
                lines.append(f"{esc(regime)}: {esc(count)} sinyal")
    return "\n".join(lines)


def failure_message(module: str, error: str, processed: str = "", report_date: str = "") -> str:
    lines = [
        "❌ <b>SDE PIPELINE FAILED</b>",
        esc(report_date or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        "",
        f"Module: <b>{esc(module)}</b>",
        f"Error: {esc(error)}",
    ]
    if processed:
        lines.append(f"Processed: {esc(processed)}")
    return "\n".join(lines)


def split_message(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, current = [], []
    length = 0
    for block in text.split("\n\n"):
        block_len = len(block) + 2
        if current and length + block_len > limit:
            chunks.append("\n\n".join(current))
            current, length = [], 0
        current.append(block)
        length += block_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def append_log(log_path: Path, event: str, status: str, detail: str = "") -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    new = pd.DataFrame([{
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "event": event,
        "status": status,
        "detail": detail,
    }])
    if log_path.exists():
        old = pd.read_csv(log_path)
        new = pd.concat([old, new], ignore_index=True)
    new.to_csv(log_path, index=False)


def console_text(text: Any) -> str:
    encoding = sys.stdout.encoding or "utf-8"
    return str(text).encode(encoding, errors="replace").decode(encoding, errors="replace")


def build_client(args, config) -> TelegramClient:
    token = args.token or os.getenv("TELEGRAM_BOT_TOKEN") or config.get("telegram", {}).get("bot_token", "")
    chat_id = args.chat_id or os.getenv("TELEGRAM_CHAT_ID") or config.get("telegram", {}).get("chat_id", "")
    return TelegramClient(token, str(chat_id), dry_run=args.dry_run, parse_mode="HTML")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stockbit SDE Telegram Bot V1.5")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--token")
    parser.add_argument("--chat-id")
    parser.add_argument("--dry-run", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_test = sub.add_parser("test")
    p_test.add_argument("--message", default="✅ SDE Telegram Bot v1 terhubung.")

    p_daily = sub.add_parser("daily")
    p_daily.add_argument("--decisions", required=True)
    p_daily.add_argument("--entry-plans")
    p_daily.add_argument("--pipeline-status", default="SUCCESS")
    p_daily.add_argument("--regime")
    p_daily.add_argument("--market-status", help="Path to MARKET_STATUS.json")
    p_daily.add_argument("--date")
    p_daily.add_argument("--top-watch", type=int, default=5)
    p_daily.add_argument("--attach", action="append", default=[])

    p_exit = sub.add_parser("exit")
    p_exit.add_argument("--alerts", required=True)

    p_weekly = sub.add_parser("weekly")
    p_weekly.add_argument("--summary", required=True)
    p_weekly.add_argument("--regime-summary")
    p_weekly.add_argument("--date")
    p_weekly.add_argument("--attach", action="append", default=[])

    p_fail = sub.add_parser("failure")
    p_fail.add_argument("--module", required=True)
    p_fail.add_argument("--error", required=True)
    p_fail.add_argument("--processed", default="")
    p_fail.add_argument("--date")

    p_swing = sub.add_parser("swing")
    p_swing.add_argument("--run-id", required=True)
    p_swing.add_argument("--run-manifest", required=True)
    p_swing.add_argument("--decisions", required=True)
    p_swing.add_argument("--entry-plans", default="")
    p_swing.add_argument("--market-status", default="")
    p_swing.add_argument("--exit-alerts", default="")
    p_swing.add_argument("--watchlist-outcomes", default="")
    p_swing.add_argument("--backtest-summary", default="")
    p_swing.add_argument("--reports-dir", default="")
    p_swing.add_argument("--preview-dir", default="")
    p_swing.add_argument("--message-max-length", type=int, default=None)
    p_swing.add_argument("--no-emoji", action="store_true")

    args = parser.parse_args()
    config = load_config(Path(args.config))
    client = build_client(args, config)
    log_path = Path(config.get("logging", {}).get("send_log", "logs/telegram_send_log.csv"))

    try:
        if args.command == "test":
            client.send_message(args.message)
            append_log(log_path, "test", "SUCCESS")
            return 0

        if args.command == "daily":
            decisions = read_csv_optional(args.decisions)
            if decisions.empty:
                raise RuntimeError("Decision CSV kosong atau tidak bisa dibaca.")
            plans = read_csv_optional(args.entry_plans)
            report_date = args.date or datetime.now().strftime("%d %B %Y")
            regime = detect_regime(decisions, args.regime or load_market_regime(args.market_status))
            technical_date, broker_date, broker_age = freshness_summary(decisions)
            decision_col = find_col(decisions, "Decision_V3", "Gated_Decision", "Decision")
            counts = decisions[decision_col].astype(str).str.upper().value_counts().to_dict() if decision_col else {}
            print(f"[TELEGRAM] source={Path(args.decisions).resolve()}", flush=True)
            print(f"[TELEGRAM] rows={len(decisions)} technical_date={technical_date} broker_date={broker_date} broker_age_bdays={broker_age}", flush=True)
            print(f"[TELEGRAM] decision_counts={counts}", flush=True)
            msg = daily_message(decisions, plans, args.pipeline_status, regime, report_date, args.top_watch)
            for part in split_message(msg):
                client.send_message(part)
            for attachment in args.attach:
                path = Path(attachment)
                if path.exists():
                    client.send_document(path, f"SDE attachment — {esc(report_date)}")
            append_log(log_path, "daily", "SUCCESS", f"rows={len(decisions)}")
            return 0

        if args.command == "exit":
            alerts = read_csv_optional(args.alerts)
            if alerts.empty:
                append_log(log_path, "exit", "SKIPPED", "no alerts")
                print("Tidak ada exit alert; Telegram tidak dikirim.")
                return 0
            for msg in exit_messages(alerts):
                client.send_message(msg)
            append_log(log_path, "exit", "SUCCESS", f"alerts={len(alerts)}")
            return 0

        if args.command == "weekly":
            summary = read_csv_optional(args.summary)
            regime_summary = read_csv_optional(args.regime_summary)
            report_date = args.date or datetime.now().strftime("%d %B %Y")
            client.send_message(weekly_message(summary, regime_summary, report_date))
            for attachment in args.attach:
                path = Path(attachment)
                if path.exists():
                    client.send_document(path, f"Weekly SDE — {esc(report_date)}")
            append_log(log_path, "weekly", "SUCCESS")
            return 0

        if args.command == "failure":
            client.send_message(failure_message(
                args.module, args.error, args.processed, args.date or ""
            ))
            append_log(log_path, "failure", "SUCCESS", args.module)
            return 0

        if args.command == "swing":
            run_manifest = load_report_json(args.run_manifest)
            run_manifest.setdefault("Run_ID", args.run_id)
            decisions = load_report_csv(args.decisions)
            entry_plans = load_report_csv(args.entry_plans)
            market_status = load_report_json(args.market_status)
            exit_alerts = load_report_csv(args.exit_alerts)
            outcomes = load_report_csv(args.watchlist_outcomes)
            backtest_summary = load_report_csv(args.backtest_summary)
            telegram_cfg = config.get("telegram", {})
            max_len = args.message_max_length or int(telegram_cfg.get("message_max_length", 3800))
            use_emoji = not args.no_emoji and bool(telegram_cfg.get("use_emoji", True))
            reports_dir = Path(args.reports_dir or f"data/output/reports/{args.run_id}")
            messages = build_all_reports(
                run_manifest,
                decisions,
                entry_plans,
                market_status,
                exit_alerts,
                outcomes,
                backtest_summary,
                max_length=max_len,
                use_emoji=use_emoji,
            )
            report_paths = write_messages(messages, reports_dir)
            if args.dry_run:
                preview_dir = Path(args.preview_dir or f"data/output/telegram_preview/{args.run_id}")
                preview_paths = write_messages(messages, preview_dir)
                for message in messages:
                    print(f"[TELEGRAM DRY RUN] {message.message_type}: {message.filename}")
                    print(console_text(message.text))
                    print("-" * 72)
                append_log(log_path, "swing", "DRY_RUN", f"preview={preview_dir}")
                return 0
            for message in messages:
                for part in split_message(message.text, max_len):
                    client.send_message(part)
            attach_cfg = config.get("attachments", {})
            for message, report_path in zip(messages, report_paths):
                should_attach = (
                    message.attach_by_default
                    or bool(attach_cfg.get(f"attach_{message.message_type}", False))
                )
                if should_attach and report_path.exists():
                    client.send_document(report_path, f"SDE Swing {message.message_type} - {args.run_id}")
            append_log(log_path, "swing", "SUCCESS", f"messages={len(messages)} reports={reports_dir}")
            return 0

    except Exception as exc:
        if args.command == "test" and str(exc) in {
            "TELEGRAM_BOT_TOKEN belum diisi.",
            "TELEGRAM_CHAT_ID belum diisi.",
        }:
            append_log(log_path, "test", "SKIPPED_NOT_CONFIGURED", str(exc))
            print(f"SKIPPED_NOT_CONFIGURED: {exc}", file=sys.stderr)
            return 10
        append_log(log_path, args.command or "unknown", "FAILED", str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
