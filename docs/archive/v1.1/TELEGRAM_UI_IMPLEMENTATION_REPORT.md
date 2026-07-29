# SDE Swing V1.5 Telegram UI Implementation Report

Tanggal implementasi: 2026-07-24

## Tujuan

Menerapkan UI Telegram profesional, ringkas, konsisten, mobile-friendly, dan aman untuk HTML Telegram pada seluruh laporan SDE Swing tanpa mengubah keputusan trading.

## Implementasi Utama

Formatter pusat baru:

```text
modules/telegram/professional_ui.py
```

Formatter tersebut digunakan oleh dua jalur eksekusi:

```text
Scheduler job runner
modules/job_runner/reports.py

Legacy/full manual Telegram runner
modules/telegram/swing_report_builder.py
```

Dengan demikian, scheduler dan full manual tidak lagi menghasilkan gaya pesan yang berbeda.

## Laporan yang Diterapkan

1. Market Outlook
2. Rekap Sinyal Harian
3. Watchlist Swing
4. Detail Sinyal Emiten
5. Data Warning
6. Closing Bell
7. Evaluasi Posisi / Watchlist Besok

Tambahan internal yang tetap tersedia:

- Pipeline Status
- Exit Alert

## Perubahan Presentation Layer

- Header `SDE SWING` pada seluruh laporan.
- Separator konsisten `━━━━━━━━━━━━━━━━━━━━━━━━━━`.
- Emoji status hijau, kuning, biru, merah, dan warning.
- Blok `<pre>` hanya untuk statistik yang membutuhkan alignment.
- Narasi tindakan dibuat terpisah dan mudah ditemukan.
- Maksimal lima saham prioritas default.
- Dynamic HTML value di-escape.
- Parse mode dibaca dari konfigurasi dan default ke `HTML`.
- Timezone menggunakan `Asia/Jakarta` dan label `WIB`.
- Pesan lebih panjang dari batas Telegram tetap memakai splitter existing.

## Status Mapping

Keputusan mesin tidak diubah.

```text
STRONG BUY  -> BUY CONFIRMED
BUY         -> WATCH HIGH
WATCH       -> WATCH
SPECULATIVE -> WATCH
AVOID       -> AVOID
```

Entry readiness ditampilkan terpisah:

```text
READY
WAITING
NOT READY
```

Jika plan ditolak, UI tidak menampilkan level Entry, TP, dan SL sebagai level valid.

## Rekap Data

- Rekap memakai simbol unik.
- Duplikat simbol dibuang berdasarkan final rank/score.
- Persentase aman ketika total emiten nol.
- Job 16:30 memakai kandidat teknikal sebagai `WATCH` sementara dan diberi label data parsial.
- Job 18:00 tetap menggunakan `FINAL_DECISION_V3` broker-confirmed.
- AVOID tidak dimasukkan ke Top Watchlist.

## File yang Ditambahkan

```text
modules/telegram/professional_ui.py
tests/test_telegram_professional_ui.py
docs/CARA_PAKAI_SDE_V1_5_TELEGRAM_UI.md
docs/TELEGRAM_UI_IMPLEMENTATION_REPORT.md
docs/TELEGRAM_UI_TEST_REPORT.md
data/output/telegram_ui_preview/scheduled/*
```

## File yang Diubah

```text
config/pipeline.json
config/telegram.json
modules/job_runner/reports.py
modules/job_runner/delivery.py
modules/telegram/swing_report_builder.py
modules/telegram/telegram_bot.py
README.md
RUN_SDE.bat
```

## Batas Perubahan

Tidak ada perubahan pada:

- technical scoring;
- broker scoring;
- broker fusion;
- final decision formula;
- liquidity guardrail;
- entry-plan formula;
- exit engine;
- analytics;
- database archive;
- pemisahan Swing dan Day Trade.
