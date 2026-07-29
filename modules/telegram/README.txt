TELEGRAM REPORTER — SDE SWING V1.5
==================================

Formatter pusat:
modules/telegram/professional_ui.py

Penggunaan utama:
- Scheduler: run_sde_job.py
- Full manual: master_pipeline.py / telegram_bot.py swing

Test koneksi : run_test_telegram.bat
Dry run      : python modules\telegram\telegram_bot.py --config config\telegram.json --dry-run swing ...
Preview UI   : python tools\generate_telegram_ui_preview.py --trade-date YYYY-MM-DD
Validasi UI  : python tools\validate_telegram_ui_preview.py

Guardrail:
- Entry/SL/TP hanya dianggap valid bila entry plan berstatus APPROVED/VALID/READY/ACTIVE.
- Plan REJECT ditampilkan sebagai ENTRY READINESS: NOT READY.
- AVOID tidak masuk Top Watchlist.
- Dynamic HTML di-escape.
- Parse mode default HTML.
- Pipeline Swing tetap terpisah dari Day Trade.
