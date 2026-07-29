#!/usr/bin/env python3
import argparse, csv, json, math, sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swing_utils import PACKAGE_VERSION, PIPELINE_VERSION, file_sha256, make_run_id, write_json


def n(v):
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return 0.0


def score(tv, lots, today, today_lots):
    s = 0
    s += 65 if tv >= 1e11 else 57 if tv >= 5e10 else 48 if tv >= 2e10 else 38 if tv >= 1e10 else 25 if tv >= 5e9 else 13 if tv >= 2e9 else 6 if tv >= 1e9 else 0
    s += 25 if lots >= 1e6 else 22 if lots >= 5e5 else 18 if lots >= 2e5 else 14 if lots >= 1e5 else 8 if lots >= 5e4 else 4 if lots >= 2.5e4 else 0
    s += 10 if today >= 1e10 and today_lots >= 1e5 else 7 if today >= 5e9 and today_lots >= 5e4 else 4 if today >= 2e9 and today_lots >= 2.5e4 else 0
    return min(100, s)


def calculate_market_status(ihsg_path: Path | None) -> dict:
    status = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date": None,
        "market_regime": "UNKNOWN",
        "ihsg_close": None,
        "ma20": None,
        "ma50": None,
        "ma20_slope": None,
        "source": str(ihsg_path) if ihsg_path else None,
        "reason": "IHSG file not supplied",
    }
    if not ihsg_path:
        return status
    if not ihsg_path.exists() or ihsg_path.stat().st_size == 0:
        status["reason"] = "IHSG file missing or empty"
        return status
    try:
        df = pd.read_csv(ihsg_path, low_memory=False)
        mapping = {str(c).strip().lower(): c for c in df.columns}
        date_col = mapping.get("date")
        close_col = mapping.get("close") or mapping.get("adj close") or mapping.get("adj_close")
        if close_col is None:
            status["reason"] = "Close column not found in IHSG file"
            return status
        work = pd.DataFrame({"Close": pd.to_numeric(df[close_col], errors="coerce")})
        if date_col is not None:
            work["Date"] = pd.to_datetime(df[date_col], errors="coerce")
            work = work.sort_values("Date")
        work = work.dropna(subset=["Close"]).reset_index(drop=True)
        if len(work) < 50:
            status["reason"] = f"Need at least 50 IHSG rows; found {len(work)}"
            return status
        work["MA20"] = work["Close"].rolling(20).mean()
        work["MA50"] = work["Close"].rolling(50).mean()
        latest = work.iloc[-1]
        prev_ma20 = work["MA20"].iloc[-6] if len(work) >= 55 else work["MA20"].iloc[-2]
        slope = float(latest["MA20"] - prev_ma20)
        close = float(latest["Close"])
        ma20 = float(latest["MA20"])
        ma50 = float(latest["MA50"])
        if close > ma20 > ma50 and slope > 0:
            regime = "BULL"
            reason = "Close > MA20 > MA50 and MA20 slope is positive"
        elif close < ma20 < ma50 and slope < 0:
            regime = "BEAR"
            reason = "Close < MA20 < MA50 and MA20 slope is negative"
        else:
            regime = "SIDEWAYS"
            reason = "Trend conditions are mixed"
        status.update({
            "date": str(latest["Date"].date()) if "Date" in latest and pd.notna(latest["Date"]) else None,
            "market_regime": regime,
            "ihsg_close": round(close, 4),
            "ma20": round(ma20, 4),
            "ma50": round(ma50, 4),
            "ma20_slope": round(slope, 4),
            "reason": reason,
        })
        return status
    except Exception as exc:
        status["reason"] = f"Failed to calculate regime: {exc}"
        return status


def main():
    parser = argparse.ArgumentParser(description="Stockbit Decision Engine V1.6")
    parser.add_argument("source", nargs="?", default="FINAL_DECISION_V2.csv")
    parser.add_argument("output", nargs="?", default="decision_v3_output")
    parser.add_argument("--ihsg", help="IHSG historical CSV with Date and Close columns")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--manifest-dir", default=None)
    parser.add_argument("--data-quality-status", default="VALID")
    args = parser.parse_args()
    args.run_id = args.run_id or make_run_id()

    src = Path(args.source)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    market_status = calculate_market_status(Path(args.ihsg) if args.ihsg else None)

    with src.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise RuntimeError("Source decision CSV is empty")

    for r in rows:
        tv = n(r.get("Turnover_MA_20")); lots = n(r.get("Volume_MA_20")) / 100
        today = n(r.get("Turnover_Value")); today_lots = n(r.get("Volume")) / 100
        ls = score(tv, lots, today, today_lots)
        tech_legacy = n(r.get("Technical_Score_Final"))
        broker = n(r.get("Broker_Score"))
        conf = str(r.get("Broker_Confirmation", "")).upper()
        # Turnover is the primary liquidity measure. Requiring a fixed lot count
        # unfairly labels high-priced liquid stocks as illiquid. Lot activity is
        # already represented in Liquidity_Score as a secondary component.
        cls = "VERY LIQUID" if tv >= 5e10 else "LIQUID" if tv >= 2e10 else "ADEQUATE" if tv >= 1e10 else "THIN" if tv >= 5e9 else "ILLIQUID"
        new_mode = any(str(r.get(key, "")).strip().lower() not in {"", "nan", "none"} for key in (
            "Technical_Quality_Score", "Entry_Readiness_PreScore", "Broker_Confidence", "Broker_Direction"
        ))

        if not new_mode:
            # Backward-compatible V1.2/V1.5 path for historical files and regression fixtures.
            tech = tech_legacy
            final = .5 * tech + .3 * broker + .2 * ls
            pen = 18 if tv < 2e9 or lots < 25000 else 10 if tv < 5e9 or lots < 50000 else 5 if tv < 1e10 or lots < 100000 else 0
            final = max(0, min(100, final + min(n(r.get("Synergy_Bonus")), 5) - n(r.get("Risk_Penalty")) - pen))
            strong_acc = conf == "STRONG ACCUMULATION"; acc = conf in ("ACCUMULATION", "STRONG ACCUMULATION")
            strong_dist = conf == "STRONG DISTRIBUTION"; dist = conf in ("DISTRIBUTION", "STRONG DISTRIBUTION")
            if strong_dist: d = "AVOID"
            elif cls == "ILLIQUID": d = "AVOID" if final < 70 else "SPECULATIVE"
            elif cls == "THIN": d = "SPECULATIVE" if final >= 60 else "AVOID"
            elif final >= 82 and tech >= 78 and strong_acc and tv >= 2e10 and lots >= 1e5 and n(r.get("Risk_Penalty")) == 0: d = "STRONG BUY"
            elif final >= 72 and tech >= 70 and acc and tv >= 1e10 and lots >= 5e4: d = "BUY"
            elif final >= 60 and not strong_dist: d = "WATCH"
            elif dist or tech >= 68: d = "SPECULATIVE"
            else: d = "AVOID"
            quality = tech
            readiness = min(tech, 70.0)
            broker_confidence = 70.0 if acc else 25.0 if dist else 50.0
            broker_direction = "ACCUMULATION" if acc else "DISTRIBUTION" if dist else "NEUTRAL"
            reason_code = "LEGACY_CLASSIFICATION"
        else:
            quality = n(r.get("Technical_Quality_Score"))
            if not math.isfinite(quality) or quality <= 0:
                quality = tech_legacy
            readiness = n(r.get("Entry_Readiness_PreScore"))
            if not math.isfinite(readiness):
                readiness = min(quality, 70.0)
            broker_confidence = n(r.get("Broker_Confidence"))
            broker_direction = str(r.get("Broker_Direction", "")).upper().strip()
            if not broker_direction:
                broker_direction = "ACCUMULATION" if "ACCUMULATION" in conf else "DISTRIBUTION" if "DISTRIBUTION" in conf else "NEUTRAL"
            if not math.isfinite(broker_confidence):
                broker_confidence = 70.0 if broker_direction == "ACCUMULATION" else 25.0 if broker_direction == "DISTRIBUTION" else 50.0
            strong_dist = conf == "STRONG DISTRIBUTION"
            dist = broker_direction == "DISTRIBUTION"
            acc = broker_direction == "ACCUMULATION"
            hard_blocker = str(r.get("Entry_Hard_Blocker", "")).strip().lower() in {"1", "true", "yes"}
            divergence = str(r.get("Broker_Divergence", "")).strip().lower() in {"1", "true", "yes"}
            risk_penalty = n(r.get("Risk_Penalty"))
            if not math.isfinite(risk_penalty):
                risk_penalty = 0.0
            synergy = min(max(n(r.get("Synergy_Bonus")), 0.0), 4.0)
            if not math.isfinite(synergy):
                synergy = 0.0
            market_penalty = 5.0 if market_status["market_regime"] == "BEAR" else 0.0
            divergence_penalty = 4.0 if divergence else 0.0
            final = max(0, min(100,
                .45 * quality + .20 * readiness + .20 * broker + .15 * ls
                + synergy - min(risk_penalty, 20.0) - market_penalty - divergence_penalty
            ))

            if hard_blocker:
                d, reason_code = "AVOID", "ENTRY_HARD_BLOCKER"
            elif strong_dist:
                d, reason_code = "AVOID", "STRONG_BROKER_DISTRIBUTION"
            elif cls == "ILLIQUID":
                d, reason_code = ("SPECULATIVE", "ILLIQUID_HIGH_SCORE") if final >= 72 else ("AVOID", "ILLIQUID")
            elif cls == "THIN":
                d, reason_code = ("WATCH HIGH", "THIN_LIQUIDITY") if final >= 68 and quality >= 72 else ("WATCH", "THIN_LIQUIDITY") if final >= 58 else ("SPECULATIVE", "THIN_LOW_SCORE")
            elif final >= 84 and quality >= 80 and readiness >= 78 and acc and broker_confidence >= 70 and not divergence:
                d, reason_code = "STRONG BUY", "QUALITY_READINESS_BROKER_ALIGNED"
            elif final >= 76 and quality >= 72 and readiness >= 70 and acc and broker_confidence >= 55:
                d, reason_code = "BUY", "VALID_BUY_CANDIDATE"
            elif final >= 68 and quality >= 70 and readiness >= 58 and not dist and broker_confidence >= 40:
                d, reason_code = "BUY CANDIDATE", "AWAITING_ENTRY_PLAN_CONFIRMATION"
            elif final >= 62 and quality >= 68 and not dist and not strong_dist:
                d, reason_code = "WATCH HIGH", "ONE_CONFIRMATION_MISSING"
            elif final >= 55 and not strong_dist:
                d, reason_code = "WATCH", "SETUP_DEVELOPING"
            elif dist or quality >= 65:
                d, reason_code = "SPECULATIVE", "RISK_OR_BROKER_MISMATCH"
            else:
                d, reason_code = "AVOID", "LOW_COMPOSITE_SCORE"

        r.update({
            "Run_ID": args.run_id,
            "Market_Regime": market_status["market_regime"],
            "Liquidity_Score": f"{ls:.2f}", "Liquidity_Class": cls,
            "Turnover_MA20_Billion": f"{tv / 1e9:.3f}", "Volume_MA20_Lots": f"{lots:.0f}",
            "Technical_Quality_Score_Final": f"{quality:.2f}",
            "Entry_Readiness_PreScore_Final": f"{readiness:.2f}",
            "Broker_Confidence_Final": f"{broker_confidence:.2f}",
            "Broker_Direction_Final": broker_direction,
            "Decision_Reason_Code": reason_code,
            "Final_Score_V3": f"{final:.2f}", "Decision_V3": d,
            "Data_Quality_Status": args.data_quality_status,
        })

    p = {"STRONG BUY": 0, "BUY": 1, "BUY CANDIDATE": 2, "WATCH HIGH": 3, "WATCH": 4, "SPECULATIVE": 5, "AVOID": 6}
    rows.sort(key=lambda r: (p[r["Decision_V3"]], -n(r["Final_Score_V3"])))
    for i, r in enumerate(rows, 1): r["Rank_V3"] = i
    lead = ["Rank_V3", "Symbol", "Decision_V3", "Decision_Reason_Code", "Market_Regime", "Final_Score_V3",
            "Technical_Quality_Score_Final", "Entry_Readiness_PreScore_Final",
            "Broker_Direction_Final", "Broker_Confidence_Final", "Liquidity_Score", "Liquidity_Class",
            "Turnover_MA20_Billion", "Volume_MA20_Lots"]
    fields = lead + [k for k in rows[0] if k not in set(lead)]
    decision_path = out / "FINAL_DECISION_V3.csv"
    with decision_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    status_path = out / "MARKET_STATUS.json"
    status_path.write_text(json.dumps(market_status, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "Run_ID": args.run_id,
        "Pipeline_Version": PIPELINE_VERSION,
        "Engine_Version": PACKAGE_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(src.resolve()),
        "source_hash": file_sha256(src),
        "output": str(decision_path.resolve()),
        "output_hash": file_sha256(decision_path),
        "market_status": str(status_path.resolve()),
        "market_status_hash": file_sha256(status_path),
        "rows": len(rows),
        "decision_counts": pd.Series([r["Decision_V3"] for r in rows]).value_counts().to_dict(),
        "Data_Quality_Status": args.data_quality_status,
    }
    write_json(out / "manifest.json", manifest)
    if args.manifest_dir:
        write_json(Path(args.manifest_dir) / f"DECISION_MANIFEST_{args.run_id}.json", manifest)
    print(decision_path)
    print(status_path)


if __name__ == "__main__":
    main()
