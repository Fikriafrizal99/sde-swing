#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.broker_fusion.broker_fusion import broker_score_frame
from modules.decision_engine.decision_engine import score as liquidity_score
from modules.exit_engine.exit_engine import load_price, build_entry_plan

SNAPSHOTS = {
    "2026-07-27": ROOT / "data/output/snapshots/2026-07-27/SWING-TECH-SNAPSHOT-20260727-234226",
    "2026-07-28": ROOT / "data/output/snapshots/2026-07-28/SWING-TECH-SNAPSHOT-20260728-163042",
    "2026-07-29": ROOT / "data/output/snapshots/2026-07-29/SWING-TECH-SNAPSHOT-20260729-185242",
    "2026-07-30": ROOT / "data/output/snapshots/2026-07-30/SWING-TECH-SNAPSHOT-20260730-205155",
    "2026-07-31": ROOT / "data/output/snapshots/2026-07-31/SWING-TECH-SNAPSHOT-20260731-163208",
}
BROKER_SUMMARY = {
    "2026-07-27": ROOT / "data/input/broker/archive/BROKER_SUMMARY_2026-07-27_235133.csv",
    "2026-07-28": ROOT / "data/input/broker/archive/BROKER_SUMMARY_2026-07-28_172133.csv",
    "2026-07-29": ROOT / "data/input/broker/archive/BROKER_SUMMARY_2026-07-29_190217.csv",
    "2026-07-30": ROOT / "data/input/broker/archive/BROKER_SUMMARY_2026-07-30_205509.csv",
    "2026-07-31": ROOT / "data/input/broker/archive/BROKER_SUMMARY_2026-07-31_183652.csv",
}
BROKER_RAW = {
    "2026-07-27": ROOT / "data/input/broker/archive/BROKER_RAW_2026-07-27_233526.csv",
    "2026-07-28": ROOT / "data/input/broker/archive/BROKER_RAW_2026-07-28_172134.csv",
    "2026-07-29": ROOT / "data/input/broker/archive/BROKER_RAW_2026-07-29_190217.csv",
    "2026-07-30": ROOT / "data/input/broker/archive/BROKER_RAW_2026-07-30_205509.csv",
    "2026-07-31": ROOT / "data/input/broker/archive/BROKER_RAW_2026-07-31_183652.csv",
}
PRICE_DIR = ROOT / "data/output/historical/by_symbol"


def num(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def truth(v: Any) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes"}


def classify_setup_overlay(row: pd.Series) -> str:
    existing = str(row.get("Setup_Type", "DEVELOPING")).upper()
    if existing != "DEVELOPING":
        return existing
    close = num(row.get("Close")); sma20 = num(row.get("SMA_20")); sma50 = num(row.get("SMA_50"))
    ema20 = num(row.get("EMA_20")); rsi = num(row.get("RSI_14")); hist = num(row.get("MACD_Hist"))
    ret20 = num(row.get("Return_20D")); vr = num(row.get("Volume_Ratio_20")); dist = num(row.get("Distance_EMA20_Pct"), 999)
    if close > 0 and sma50 > 0 and close >= sma50 and abs(dist) <= 4.5 and 42 <= rsi <= 64 and hist >= -0.02 * max(close, 1) and ret20 > -2 and 0.65 <= vr <= 2.2:
        return "EARLY_ACCUMULATION"
    if close > sma20 > sma50 and dist <= 6 and rsi <= 72:
        return "CONTINUATION"
    return existing


def foreign_frame(raw: pd.DataFrame) -> pd.DataFrame:
    work = raw.copy()
    work["Symbol"] = work["SYMBOL"].astype(str).str.upper().str.strip().str.replace(".JK", "", regex=False)
    work["BROKER_TYPE_N"] = work["BROKER_TYPE"].astype(str).str.upper().str.strip()
    work["NET_VALUE"] = pd.to_numeric(work["NET_VALUE"], errors="coerce").fillna(0.0)
    work["ABS_NET"] = work["NET_VALUE"].abs()
    work["IS_FOREIGN"] = work["BROKER_TYPE_N"].eq("ASING")
    work["IS_DOMESTIC"] = ~work["IS_FOREIGN"]
    records = []
    for symbol, group in work.groupby("Symbol"):
        foreign = group[group["IS_FOREIGN"]]
        domestic = group[group["IS_DOMESTIC"]]
        fnet = foreign["NET_VALUE"].sum(); fgross = foreign["ABS_NET"].sum()
        dnet = domestic["NET_VALUE"].sum(); dgross = domestic["ABS_NET"].sum()
        fpct = 100 * fnet / fgross if fgross else 0.0
        dpct = 100 * dnet / dgross if dgross else 0.0
        fparticipation = fgross / group["ABS_NET"].sum() if group["ABS_NET"].sum() else 0.0
        fscore = float(np.clip(50 + 3.0 * fpct, 0, 100))
        dscore = float(np.clip(50 + 3.0 * dpct, 0, 100))
        fconf = float(np.clip(25 + 50 * fparticipation + min(abs(fpct), 20) * 1.25, 0, 100))
        fdir = "POSITIVE" if fpct >= 1 else "NEGATIVE" if fpct <= -1 else "NEUTRAL"
        records.append({
            "Symbol": symbol,
            "Foreign_Net_Value": fnet,
            "Foreign_Gross_Value": fgross,
            "Foreign_Net_Pct": fpct,
            "Foreign_Score": fscore,
            "Foreign_Confidence": fconf,
            "Foreign_Direction": fdir,
            "Domestic_Net_Value": dnet,
            "Domestic_Gross_Value": dgross,
            "Domestic_Net_Pct": dpct,
            "Domestic_Flow_Score": dscore,
            "Foreign_Participation": fparticipation,
        })
    return pd.DataFrame(records)


def fuse(date: str, top_n: int = 30) -> tuple[pd.DataFrame, pd.DataFrame]:
    ranking = pd.read_csv(SNAPSHOTS[date] / "technical_ranking_full.csv", low_memory=False)
    top = pd.read_csv(SNAPSHOTS[date] / "technical_candidates_top30.csv", low_memory=False).head(top_n)
    summary = pd.read_csv(BROKER_SUMMARY[date], low_memory=False)
    summary["Symbol"] = summary["EMITEN"].astype(str).str.upper().str.strip().str.replace(".JK", "", regex=False)
    scored = broker_score_frame(summary)
    raw = pd.read_csv(BROKER_RAW[date], low_memory=False)
    foreign = foreign_frame(raw)
    merged = top.merge(scored, on="Symbol", how="left", suffixes=("", "_BROKER"), indicator=True)
    merged["Broker_Data_Available"] = merged["_merge"].eq("both")
    merged = merged.drop(columns="_merge").merge(foreign, on="Symbol", how="left")
    for c, default in {
        "Broker_Score": 0.0, "Broker_Confidence": 0.0, "Broker_Direction": "NEUTRAL",
        "Broker_Confirmation": "NO DATA", "Broker_Divergence": False,
        "Broker_Direction_Score": 0.0, "Foreign_Score": 50.0,
        "Foreign_Confidence": 0.0, "Foreign_Direction": "NO DATA",
        "Domestic_Flow_Score": 50.0, "Foreign_Net_Pct": 0.0,
        "Domestic_Net_Pct": 0.0,
    }.items():
        if c not in merged: merged[c] = default
        merged[c] = merged[c].fillna(default)
    return ranking, merged


def baseline_decision(row: pd.Series, market_regime: str = "BULL") -> dict[str, Any]:
    tv = num(row.get("Turnover_MA_20")); lots = num(row.get("Volume_MA_20")) / 100
    today = num(row.get("Turnover_Value")); today_lots = num(row.get("Volume")) / 100
    ls = liquidity_score(tv, lots, today, today_lots)
    cls = "VERY LIQUID" if tv >= 5e10 else "LIQUID" if tv >= 2e10 else "ADEQUATE" if tv >= 1e10 else "THIN" if tv >= 5e9 else "ILLIQUID"
    quality = num(row.get("Technical_Quality_Score")); readiness = num(row.get("Entry_Readiness_PreScore"))
    broker = num(row.get("Broker_Score")); bconf = num(row.get("Broker_Confidence")); bdir = str(row.get("Broker_Direction", "NEUTRAL")).upper()
    confirmation = str(row.get("Broker_Confirmation", "NO DATA")).upper()
    divergence = truth(row.get("Broker_Divergence")); hard = truth(row.get("Entry_Hard_Blocker"))
    synergy = 4.0 if bdir == "ACCUMULATION" and bconf >= 70 and quality >= 78 and readiness >= 70 else 2.0 if bdir == "ACCUMULATION" and bconf >= 50 and quality >= 70 else 0.0
    risk = 0.0
    if not truth(row.get("Broker_Data_Available")): risk += 15
    if confirmation == "STRONG DISTRIBUTION": risk += 20
    elif confirmation == "DISTRIBUTION": risk += 10
    if divergence: risk += 4
    if hard: risk += 20
    final = float(np.clip(.45*quality + .20*readiness + .20*broker + .15*ls + synergy - min(risk,20) - (5 if market_regime == "BEAR" else 0) - (4 if divergence else 0),0,100))
    rejected=[]
    if hard: decision, reason = "AVOID", "ENTRY_HARD_BLOCKER"; rejected.append(str(row.get("Entry_Hard_Blocker_Reason") or "ENTRY_HARD_BLOCKER"))
    elif confirmation == "STRONG DISTRIBUTION": decision, reason = "AVOID", "STRONG_BROKER_DISTRIBUTION"; rejected.append(reason)
    elif cls == "ILLIQUID": decision, reason = (("SPECULATIVE", "ILLIQUID_HIGH_SCORE") if final >=72 else ("AVOID","ILLIQUID")); rejected.append("LIQUIDITY_GATE")
    elif cls == "THIN": decision, reason = (("WATCH HIGH","THIN_LIQUIDITY") if final>=68 and quality>=72 else ("WATCH","THIN_LIQUIDITY") if final>=58 else ("SPECULATIVE","THIN_LOW_SCORE")); rejected.append("THIN_LIQUIDITY")
    elif final>=84 and quality>=80 and readiness>=78 and bdir=="ACCUMULATION" and bconf>=70 and not divergence: decision,reason="STRONG BUY","QUALITY_READINESS_BROKER_ALIGNED"
    elif final>=76 and quality>=72 and readiness>=70 and bdir=="ACCUMULATION" and bconf>=55: decision,reason="BUY","VALID_BUY_CANDIDATE"
    elif final>=68 and quality>=70 and readiness>=58 and bdir!="DISTRIBUTION" and bconf>=40: decision,reason="BUY CANDIDATE","AWAITING_ENTRY_PLAN_CONFIRMATION"; rejected.append("ENTRY_NOT_TRIGGERED")
    elif final>=62 and quality>=68 and bdir!="DISTRIBUTION" and confirmation!="STRONG DISTRIBUTION": decision,reason="WATCH HIGH","ONE_CONFIRMATION_MISSING"; rejected.append("ONE_CONFIRMATION_MISSING")
    elif final>=55 and confirmation!="STRONG DISTRIBUTION": decision,reason="WATCH","SETUP_DEVELOPING"; rejected.append("COMPOSITE_BELOW_BUY_THRESHOLD")
    elif bdir=="DISTRIBUTION" or quality>=65: decision,reason="SPECULATIVE","RISK_OR_BROKER_MISMATCH"; rejected.append("BROKER_OR_RISK_MISMATCH")
    else: decision,reason="AVOID","LOW_COMPOSITE_SCORE"; rejected.append(reason)
    return {"Baseline_Decision":decision,"Baseline_Final_Score":round(final,2),"Baseline_Reason":reason,"Liquidity_Score":ls,"Liquidity_Class":cls,"Baseline_Rejected_By":rejected,
            "Synergy_Bonus":synergy,"Risk_Penalty":risk}


def setup_profile(setup: str) -> dict[str,float]:
    return {
        "BREAKOUT":{"ready":73,"trigger":65,"tech":67,"readiness":66,"hard_ext":3.3,"soft_ext":2.6},
        "PULLBACK":{"ready":72,"trigger":64,"tech":67,"readiness":64,"hard_ext":2.8,"soft_ext":2.2},
        "TREND_CONTINUATION":{"ready":73,"trigger":65,"tech":68,"readiness":65,"hard_ext":3.0,"soft_ext":2.5},
        "CONTINUATION":{"ready":73,"trigger":65,"tech":68,"readiness":65,"hard_ext":3.0,"soft_ext":2.5},
        "EARLY_ACCUMULATION":{"ready":76,"trigger":63,"tech":61,"readiness":50,"hard_ext":2.6,"soft_ext":2.0},
        "DEVELOPING":{"ready":76,"trigger":66,"tech":67,"readiness":58,"hard_ext":3.0,"soft_ext":2.4},
    }.get(setup,{"ready":74,"trigger":65,"tech":66,"readiness":60,"hard_ext":3.0,"soft_ext":2.4})


def patched_decision(row: pd.Series, relative_rank_pct: float, plan: dict[str,Any] | None = None, market_regime: str="BULL") -> dict[str,Any]:
    setup = classify_setup_overlay(row)
    p = setup_profile(setup)
    q=num(row.get("Technical_Quality_Score")); ready=num(row.get("Entry_Readiness_PreScore")); tv=num(row.get("Turnover_MA_20")); lots=num(row.get("Volume_MA_20"))/100
    today=num(row.get("Turnover_Value")); today_lots=num(row.get("Volume"))/100; liq=liquidity_score(tv,lots,today,today_lots)
    lclass="VERY LIQUID" if tv>=5e10 else "LIQUID" if tv>=2e10 else "ADEQUATE" if tv>=1e10 else "THIN" if tv>=2e9 else "ILLIQUID"
    broker_structure = float(np.clip(50 + (num(row.get("Broker_Concentration_Component"))+num(row.get("Broker_Pattern_Component")))/2,0,100))
    domestic=num(row.get("Domestic_Flow_Score"),50); bscore=.55*broker_structure+.45*domestic
    fscore=num(row.get("Foreign_Score"),50); fconf=num(row.get("Foreign_Confidence")); fweight=min(fconf/60,1)
    foreign_effective=50+(fscore-50)*fweight
    rank_score=float(np.clip(100-relative_rank_pct,0,100))
    final=.42*q+.18*ready+.10*liq+.17*bscore+.08*foreign_effective+.05*rank_score
    soft=[]; hard=[]; trace=[]
    atr_ext=num(row.get("ATR_Extension"),0)
    bconf=num(row.get("Broker_Confidence")); bdir=str(row.get("Broker_Direction","NEUTRAL")).upper(); bdir_score=num(row.get("Broker_Direction_Score"))
    if truth(row.get("Entry_Hard_Blocker")): hard.append(str(row.get("Entry_Hard_Blocker_Reason") or "ENTRY_HARD_BLOCKER"))
    if tv<2e9: hard.append("LIQUIDITY_VERY_POOR")
    if bdir=="DISTRIBUTION" and bconf>=70 and bdir_score<=-60: hard.append("STRONG_BROKER_DISTRIBUTION")
    if atr_ext>p["hard_ext"]: hard.append("PRICE_EXTENDED_HARD")
    elif atr_ext>p["soft_ext"]: soft.append("PRICE_EXTENDED_SOFT"); final-=5
    if bdir=="DISTRIBUTION" and "STRONG_BROKER_DISTRIBUTION" not in hard: soft.append("BROKER_DISTRIBUTION"); final-=6
    if fscore<35 and fconf>=55: soft.append("FOREIGN_NEGATIVE"); final-=4
    if truth(row.get("Broker_Divergence")): soft.append("BROKER_DIVERGENCE"); final-=3
    if market_regime=="BEAR": soft.append("BEAR_MARKET_PENALTY"); final-=4 if setup=="BREAKOUT" else 2
    final=float(np.clip(final,0,100))
    plan_status=str((plan or {}).get("Plan_Status", "UNKNOWN")).upper(); plan_reason=str((plan or {}).get("Rejection_Reason", ""))
    rr_major=num((plan or {}).get("RR_To_Major_Resistance"), math.nan)
    if plan_status=="REJECT" and plan_reason in {"INVALID_STOP","NO_VALID_RESISTANCE_PATH"}: hard.append(plan_reason)
    if math.isfinite(rr_major) and rr_major<0.9: hard.append("RISK_REWARD_BELOW_MINIMUM")
    trigger_ready = plan_status=="ACCEPT" or (setup=="BREAKOUT" and truth(row.get("Breakout_20D")) and num(row.get("Volume_Ratio_20"))>=1.2)
    if hard:
        decision="AVOID"
    elif final>=p["ready"] and q>=p["tech"] and ready>=p["readiness"] and trigger_ready and setup!="EARLY_ACCUMULATION":
        decision="BUY READY"
    elif final>=p["trigger"] and q>=p["tech"] and ready>=max(45,p["readiness"]-12):
        decision="BUY ON TRIGGER"
        if not trigger_ready: soft.append("ENTRY_NOT_TRIGGERED")
    elif final>=55 and q>=58:
        decision="WATCH"
    else:
        decision="AVOID"; hard.append("LOW_COMPOSITE_SCORE")
    rejected=list(dict.fromkeys(hard+soft))
    trace += [f"SETUP={setup}",f"TECH={q:.1f}",f"READINESS={ready:.1f}",f"BROKER_DECORR={bscore:.1f}",f"FOREIGN={foreign_effective:.1f}",f"LIQ={liq:.1f}",f"RANK_PCT={relative_rank_pct:.1f}",f"FINAL={final:.1f}",f"PLAN={plan_status}"]
    return {"Patched_Decision":decision,"Patched_Final_Score":round(final,2),"Patched_Setup_Type":setup,"Patched_Rejected_By":rejected,"Hard_Blockers":hard,"Soft_Penalties":soft,"Decision_Trace":trace,
            "Broker_Score_Decorrelated":round(bscore,2),"Foreign_Score_Effective":round(foreign_effective,2),"Liquidity_Class_Patched":lclass}


def truncate_price(symbol: str, date: str) -> pd.DataFrame | None:
    p=PRICE_DIR/f"{symbol}.csv"
    if not p.exists(): return None
    px=load_price(p)
    px=px[pd.to_datetime(px["Date"])<=pd.Timestamp(date)].copy()
    return px if len(px)>=30 else None


def plan_baseline(row: pd.Series,date:str) -> dict[str,Any] | None:
    px=truncate_price(str(row["Symbol"]),date)
    if px is None: return None
    r=row.copy(); r["Decision"]=baseline_decision(row)["Baseline_Decision"]; r["Market_Regime"]="BULL"; r["Liquidity_Class"]=baseline_decision(row)["Liquidity_Class"]
    try: return build_entry_plan(r,px,1.0,2.0,7.0,20)
    except Exception: return None


def patched_plan_from_baseline(row:pd.Series,date:str) -> dict[str,Any] | None:
    # Start from the proven price/ATR plumbing, then repair the resistance and hard-gate semantics.
    plan=plan_baseline(row,date)
    if not plan: return None
    px=truncate_price(str(row["Symbol"]),date)
    entry=num(plan.get("Entry_Reference_Price")); risk=num(plan.get("Risk_Per_Share")); setup=classify_setup_overlay(row)
    if px is not None and risk>0:
        highs=pd.to_numeric(px["High"],errors="coerce")
        piv=(highs>highs.shift(1))&(highs>=highs.shift(2))&(highs>highs.shift(-1))&(highs>=highs.shift(-2))
        levels=sorted(float(v) for v in highs[piv].dropna() if float(v)>entry*1.001)
        clustered=[]
        for v in levels:
            if not clustered or abs(v/clustered[-1]-1)>.012: clustered.append(v)
        minor=clustered[0] if clustered else math.nan
        major=next((v for v in clustered if (v-entry)/risk>=1.0),math.nan)
        plan["Minor_Resistance"]=minor; plan["Major_Resistance"]=major
        plan["RR_To_Minor_Resistance"]=(minor-entry)/risk if math.isfinite(minor) else math.nan
        plan["RR_To_Major_Resistance"]=(major-entry)/risk if math.isfinite(major) else math.nan
        # Targets remain R-based but are capped/anchored to a confirmed major resistance where available.
        t1=entry+risk*1.0; t2=entry+risk*2.0
        if math.isfinite(major):
            plan["Target_1"]=min(t1,major) if major>entry else t1
            plan["Target_2"]=major if 1.0<=(major-entry)/risk<=2.5 else t2
        # Bear is a penalty, not an unconditional rejection; THIN is trigger-only.
        reason=str(plan.get("Rejection_Reason", ""))
        if reason=="BEARISH_MARKET_REGIME": plan["Plan_Status"]="CONDITIONAL"; plan["Rejection_Reason"]="BEAR_MARKET_TRIGGER_REQUIRED"
        if reason=="LIQUIDITY_GATE" and num(row.get("Turnover_MA_20"))>=2e9: plan["Plan_Status"]="CONDITIONAL"; plan["Rejection_Reason"]="THIN_LIQUIDITY_TRIGGER_REQUIRED"
        p=setup_profile(setup); ext=num(row.get("ATR_Extension"))
        if reason=="PRICE_EXTENDED" and ext<=p["hard_ext"]: plan["Plan_Status"]="CONDITIONAL"; plan["Rejection_Reason"]="WAIT_FOR_PULLBACK"
        if math.isfinite(plan["RR_To_Major_Resistance"]) and plan["RR_To_Major_Resistance"]>=1.0 and plan.get("Plan_Status")=="REJECT" and reason=="NO_VALID_RESISTANCE_PATH":
            plan["Plan_Status"]="CONDITIONAL"; plan["Rejection_Reason"]="WAIT_FOR_ENTRY_TRIGGER"
    return plan


def outcome(symbol:str,date:str,entry:float,stop:float|None=None,target1:float|None=None) -> dict[str,Any]:
    p=PRICE_DIR/f"{symbol}.csv"
    if not p.exists() or not entry: return {}
    d=pd.read_csv(p); d["Date"]=pd.to_datetime(d["Date"]); future=d[d["Date"]>pd.Timestamp(date)].head(5).copy()
    if future.empty: return {"Available_Forward_Days":0}
    highs=pd.to_numeric(future["High"],errors="coerce"); lows=pd.to_numeric(future["Low"],errors="coerce"); closes=pd.to_numeric(future["Close"],errors="coerce")
    stop_hit=bool((lows<=stop).any()) if stop and math.isfinite(stop) else False
    t1_hit=bool((highs>=target1).any()) if target1 and math.isfinite(target1) else False
    return {"Available_Forward_Days":len(future),"MFE_1_5D_Pct":round((highs.max()/entry-1)*100,3),"MAE_1_5D_Pct":round((lows.min()/entry-1)*100,3),"Close_Return_Available_Pct":round((closes.iloc[-1]/entry-1)*100,3),"Stop_Hit":stop_hit,"Target1_Hit":t1_hit,"Forward_End_Date":future["Date"].iloc[-1].date().isoformat()}


def main() -> int:
    outdir=ROOT/"data/output/audit_v162"; outdir.mkdir(parents=True,exist_ok=True)
    allrows=[]; funnels=[]; raw_checks=[]; pool_rows=[]
    for date in SNAPSHOTS:
        ranking, merged=fuse(date)
        ranking=ranking.copy(); ranking["Relative_Rank_Pct"]=ranking["Technical_Quality_Score"].rank(method="min",ascending=False,pct=True)*100
        # Candidate pool expansion analysis independent of missing broker coverage beyond top30.
        ranking["Overlay_Setup"]=ranking.apply(classify_setup_overlay,axis=1)
        mins=ranking["Overlay_Setup"].map({"BREAKOUT":60,"PULLBACK":58,"TREND_CONTINUATION":60,"CONTINUATION":60,"EARLY_ACCUMULATION":56,"DEVELOPING":62}).fillna(62)
        eligible50=ranking[(ranking["Candidate_Status"]=="PASS")&(ranking["Technical_Quality_Score"]>=mins)&(ranking["Relative_Rank_Pct"]<=15)].head(50)
        pool_rows.append(pd.DataFrame({"Date":date,"Symbol":eligible50["Symbol"],"Rank":range(1,len(eligible50)+1),"Technical_Quality_Score":eligible50["Technical_Quality_Score"],"Setup_Type":eligible50["Overlay_Setup"]}))
        rankmap=ranking.set_index("Symbol")["Relative_Rank_Pct"].to_dict()
        plans_base={}; plans_patch={}
        dayrows=[]
        for _,row in merged.iterrows():
            symbol=str(row["Symbol"]); base=baseline_decision(row); pb=plan_baseline(row,date); pp=patched_plan_from_baseline(row,date)
            plans_base[symbol]=pb; plans_patch[symbol]=pp
            patch=patched_decision(row,rankmap.get(symbol,100),pp)
            record={"Date":date,"Symbol":symbol,"Setup_Type_Baseline":row.get("Setup_Type"),"Technical_Score":num(row.get("Technical_Quality_Score")),"Entry_Readiness":num(row.get("Entry_Readiness_PreScore")),"Broker_Score":num(row.get("Broker_Score")),"Broker_Direction":row.get("Broker_Direction"),"Broker_Confidence":num(row.get("Broker_Confidence")),"Foreign_Score":num(row.get("Foreign_Score"),50),"Foreign_Net_Pct":num(row.get("Foreign_Net_Pct")),"ATR_Extension":num(row.get("ATR_Extension")),"Relative_Rank_Pct":rankmap.get(symbol,100),**base,**patch}
            if pb:
                record.update({"Baseline_Plan_Status":pb.get("Plan_Status"),"Baseline_Plan_Reason":pb.get("Rejection_Reason"),"Baseline_Entry":pb.get("Entry_Reference_Price"),"Baseline_Stop":pb.get("Initial_Stop"),"Baseline_Target1":pb.get("Target_1"),"Baseline_RR_Major":pb.get("RR_To_Major_Resistance")})
            if pp:
                record.update({"Patched_Plan_Status":pp.get("Plan_Status"),"Patched_Plan_Reason":pp.get("Rejection_Reason"),"Patched_Entry":pp.get("Entry_Reference_Price"),"Patched_Stop":pp.get("Initial_Stop"),"Patched_Target1":pp.get("Target_1"),"Patched_RR_Major":pp.get("RR_To_Major_Resistance")})
            entry=num((pp or pb or {}).get("Entry_Reference_Price"),num(row.get("Close")))
            record.update(outcome(symbol,date,entry,num((pp or {}).get("Initial_Stop"),math.nan),num((pp or {}).get("Target_1"),math.nan)))
            dayrows.append(record); allrows.append(record)
        daydf=pd.DataFrame(dayrows)
        # Explicit funnel: gate counts are stage survivor counts plus failure counts.
        liquidity_pass=int((pd.to_numeric(ranking["Turnover_MA_20"],errors="coerce")>=1e9).sum())
        technical_pass=int(((ranking["Candidate_Status"]=="PASS")&(ranking["Technical_Quality_Score"]>=60)).sum())
        brokers=int(merged["Broker_Data_Available"].sum())
        broker_non_dist=int((merged["Broker_Direction"].astype(str).str.upper()!="DISTRIBUTION").sum())
        foreign_fail=int(((merged["Foreign_Score"]<35)&(merged["Foreign_Confidence"]>=55)).sum())
        extension_fail=int((pd.to_numeric(merged["ATR_Extension"],errors="coerce")>2.5).sum())
        rr_fail=int(daydf.get("Baseline_Plan_Reason",pd.Series(dtype=str)).eq("NO_VALID_RESISTANCE_PATH").sum())
        counts=daydf["Patched_Decision"].value_counts()
        basecounts=daydf["Baseline_Decision"].value_counts()
        funnels.append({"Date":date,"Universe":len(ranking),"Liquidity_Pass_1B":liquidity_pass,"Technical_Pass_60":technical_pass,"Selected_Top30":len(merged),"Broker_Matched":brokers,"Broker_Not_Distribution":broker_non_dist,"Foreign_Strong_Negative":foreign_fail,"Extension_Above_2_5ATR":extension_fail,"RR_Fail_Baseline_Plans":rr_fail,"Baseline_BUY_or_STRONG":int(basecounts.get("BUY",0)+basecounts.get("STRONG BUY",0)),"Baseline_BUY_CANDIDATE":int(basecounts.get("BUY CANDIDATE",0)),"Patched_BUY_READY":int(counts.get("BUY READY",0)),"Patched_BUY_ON_TRIGGER":int(counts.get("BUY ON TRIGGER",0)),"Patched_WATCH":int(counts.get("WATCH",0)),"Patched_AVOID":int(counts.get("AVOID",0)),"Expanded_Technical_Pool":len(eligible50)})
        # Raw-summary integrity.
        s=pd.read_csv(BROKER_SUMMARY[date]); r=pd.read_csv(BROKER_RAW[date]); ag=r.groupby("SYMBOL")["NET_VALUE"].sum(); ss=s.set_index("EMITEN")["NET_FLOW"]
        common=ss.index.intersection(ag.index); delta=(ss.loc[common]-ag.loc[common]).abs()
        raw_checks.append({"Date":date,"Summary_Rows":len(s),"Raw_Rows":len(r),"Common_Symbols":len(common),"NetFlow_Exact_Matches":int((delta<1).sum()),"NetFlow_Max_Abs_Diff":float(delta.max() if len(delta) else math.nan),"Raw_Foreign_Rows":int(r["BROKER_TYPE"].astype(str).str.upper().eq("ASING").sum()),"Missing_Summary_Symbols_In_Raw":int(len(ss.index.difference(ag.index)))})
    details=pd.DataFrame(allrows)
    # Classification labels on available outcomes; threshold is explicitly audit-only, not strategy truth.
    details["Audit_Outcome_Label"]="UNVALIDATED"
    has=details["Available_Forward_Days"].fillna(0)>0
    neg=details["Baseline_Decision"].isin(["AVOID","SPECULATIVE","WATCH","WATCH HIGH"])
    pos=details["Baseline_Decision"].isin(["BUY","STRONG BUY"])
    details.loc[has&neg&(details["MFE_1_5D_Pct"]>=3)&(details["MAE_1_5D_Pct"]>-5),"Audit_Outcome_Label"]="FALSE_NEGATIVE_CANDIDATE"
    details.loc[has&pos&((details["Stop_Hit"]==True)|((details["Close_Return_Available_Pct"]<0)&(details["MFE_1_5D_Pct"]<2))),"Audit_Outcome_Label"]="FALSE_POSITIVE_CANDIDATE"
    details.loc[has&(~details["Audit_Outcome_Label"].isin(["FALSE_NEGATIVE_CANDIDATE","FALSE_POSITIVE_CANDIDATE"])),"Audit_Outcome_Label"]="NO_CLEAR_ERROR"
    # Serialization for list columns.
    for c in ["Baseline_Rejected_By","Patched_Rejected_By","Hard_Blockers","Soft_Penalties","Decision_Trace"]:
        details[c]=details[c].apply(lambda x: json.dumps(x,ensure_ascii=False) if isinstance(x,list) else x)
    details.to_csv(outdir/"decision_before_after.csv",index=False)
    pd.DataFrame(funnels).to_csv(outdir/"funnel_by_date.csv",index=False)
    pd.DataFrame(raw_checks).to_csv(outdir/"broker_raw_integrity.csv",index=False)
    pd.concat(pool_rows,ignore_index=True).to_csv(outdir/"expanded_candidate_pool.csv",index=False)
    # Gate frequency from patched and baseline.
    gates=Counter()
    for col in ["Baseline_Rejected_By","Patched_Rejected_By"]:
        for raw in details[col].dropna():
            try: vals=json.loads(raw)
            except Exception: vals=[]
            for v in vals: gates[(col,v)]+=1
    gate_df=pd.DataFrame([{"Model":k[0],"Gate":k[1],"Count":v} for k,v in gates.items()]).sort_values(["Model","Count"],ascending=[True,False])
    gate_df.to_csv(outdir/"gate_failures.csv",index=False)
    # Examples of rejected records exactly in requested structure.
    examples=[]
    for _,r in details.sort_values(["Date","Patched_Final_Score"],ascending=[True,False]).iterrows():
        if r["Patched_Decision"] not in {"BUY READY"}:
            examples.append({"date":r["Date"],"symbol":r["Symbol"],"setup_type":r["Patched_Setup_Type"],"technical_score":r["Technical_Score"],"broker_score":r["Broker_Score"],"foreign_score":r["Foreign_Score"],"final_score":r["Patched_Final_Score"],"decision":r["Patched_Decision"],"rejected_by":json.loads(r["Patched_Rejected_By"])})
    (outdir/"rejected_by_examples.json").write_text(json.dumps(examples,ensure_ascii=False,indent=2),encoding="utf-8")
    # Summary json.
    summary={
        "dates":list(SNAPSHOTS),"rows_evaluated":len(details),"available_forward_rows":int(has.sum()),
        "max_forward_days":int(details["Available_Forward_Days"].fillna(0).max()),
        "baseline_decisions":details["Baseline_Decision"].value_counts().to_dict(),
        "patched_decisions":details["Patched_Decision"].value_counts().to_dict(),
        "outcome_labels":details["Audit_Outcome_Label"].value_counts().to_dict(),
        "false_negative_examples":details[details["Audit_Outcome_Label"]=="FALSE_NEGATIVE_CANDIDATE"].sort_values("MFE_1_5D_Pct",ascending=False)[["Date","Symbol","Baseline_Decision","Patched_Decision","MFE_1_5D_Pct","MAE_1_5D_Pct","Technical_Score","Broker_Direction","Foreign_Net_Pct"]].head(20).to_dict("records"),
        "false_positive_examples":details[details["Audit_Outcome_Label"]=="FALSE_POSITIVE_CANDIDATE"][["Date","Symbol","Baseline_Decision","Patched_Decision","MFE_1_5D_Pct","MAE_1_5D_Pct","Stop_Hit"]].to_dict("records"),
    }
    (outdir/"audit_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
