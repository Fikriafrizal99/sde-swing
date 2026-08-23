TELEGRAM REPORTING — SDE SWING V1.7.1
=====================================

Jalur aktif:
engine-owned artifact
-> enhanced report builder
-> current presentation formatter
-> ReportPayload
-> modules/job_runner/delivery.py
-> TelegramRouter
-> Telegram topic

Presentation aktif utama:
- Market Outlook       : modules/telegram/market_outlook_ui.py
- Post Market          : modules/telegram/post_market_ui.py
- Final Watchlist      : modules/telegram/final_watchlist_ui.py
- Shared/operational   : modules/telegram/daily_report_ui.py
- Heatmap              : modules/telegram/market_heatmap.py

Routing aktif:
- Market / Post Market / Broker / Final Watchlist : topic 9
- Signal Detail                                : topic 6
- Evaluation / operational report             : topic 701
- System                                      : topic 5
- News                                        : topic 1451

Maintenance Telegram:
- Validasi credential : python tools\telegram_settings.py validate-credentials
- Validasi semua topic: python tools\telegram_settings.py test-all
- Status konfigurasi  : python tools\telegram_settings.py status

Preview/report aktif harus mengikuti enhanced runtime/report builder. Jangan
menambahkan launcher baru yang memanggil telegram_bot.py secara langsung.

COMPATIBILITY ONLY
------------------
modules/telegram/telegram_bot.py dan modules/telegram/swing_report_builder.py
masih dipertahankan hanya karena deprecated master_pipeline.py masih memiliki
kontrak kompatibilitas Full Manual/regression. Keduanya bukan jalur operasional
utama dan tidak boleh menjadi sumber presentation baru.

Guardrail:
- Reporting/presentation tidak boleh mengubah engine decision, score, threshold,
  entry, SL, TP1, TP2, risk/reward, atau lifecycle semantics.
- Dynamic HTML di-escape.
- Parse mode default HTML.
- Final Watchlist Summary/Detail/CSV harus berada pada topic Final Watchlist yang
  sama.
- Legacy TELEGRAM_THREAD_SIGNAL_ID hanya compatibility fallback dan tidak boleh
  menimpa route spesifik.
