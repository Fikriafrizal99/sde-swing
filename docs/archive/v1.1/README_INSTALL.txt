SDE v5.1 SAFE BROKER AUTOMATION PATCH

Patch ini hanya menambahkan otomasi broker dan mengubah orchestration pipeline.
Patch ini TIDAK membawa atau menimpa:
- modules/historical_downloader/historical_downloader.py
- modules/market_data/update_ihsg.py
- modules/telegram/telegram_bot.py
- modules/decision_engine/decision_engine.py
- modules/exit_engine/exit_engine.py
- data input/output/state yang sudah ada

Sebelum instalasi:
1. Pastikan v4.2 Full Refresh Fix dan v4.3 Telegram Freshness Fix sudah terpasang.
2. Backup master_pipeline.py dan config/pipeline.json.
3. Copy isi patch ke root project dan replace file yang diminta.
4. Update Tampermonkey memakai file pada folder tampermonkey.

File baru:
- modules/broker_bridge/wait_for_broker_export.py
- modules/decision_source_builder/build_decision_source.py
- tampermonkey/Stockbit_Broker_Summary_Auto_Navigator_v3.1.user.js

File yang sengaja diubah:
- master_pipeline.py
- config/pipeline.json
