DECISION ENGINE — SDE SWING V1.6
================================

Input : data\input\FINAL_DECISION_V2.csv
Output: data\output\decision\FINAL_DECISION_V3.csv
        data\output\decision\MARKET_STATUS.json

Komponen V1.6:
- Technical Quality
- Entry Readiness
- Broker Score dan Broker Confidence
- Liquidity
- Market Regime
- Synergy Bonus dan Risk Penalty

Status:
STRONG BUY, BUY, BUY CANDIDATE, WATCH HIGH, WATCH, SPECULATIVE, AVOID.

BUY CONFIRMED bukan label langsung Decision Engine. Status tersebut hanya tampil
di Telegram ketika BUY/STRONG BUY juga memiliki Entry Plan ACCEPT.

Jalur legacy tetap tersedia untuk file historis yang belum memiliki field V1.6.
Penggunaan resmi melalui RUN_SDE.bat atau master_pipeline.py.
