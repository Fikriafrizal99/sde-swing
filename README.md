# SDE Swing V1.6.1 Telegram Broker Detail & Control Panel

Versi paket: `1.6.1`  
Pipeline ID: `SDE_SWING_V1_6_1_TELEGRAM_BROKER_DETAIL_CONTROL_PANEL`

V1.6.1 melanjutkan mesin sinyal V1.6.0 dan merapikan dua area penggunaan harian: **laporan Telegram** dan **launcher BAT**. Format Market Outlook tidak diubah.

## Telegram

### Market Outlook — 07:30

Format, isi, dan alur Market Outlook tetap seperti V1.6.0.

### Post Market — 16:30

Post Market sekarang menjadi satu pesan ringkas yang berisi:

- kondisi IHSG;
- jumlah data yang diproses;
- jumlah kandidat teknikal;
- Ready Zone, Developing, dan Extended;
- maksimal lima kandidat teknikal utama;
- penegasan bahwa keputusan final menunggu Broker Summary.

Post Market tidak lagi mengirim Closing Bell dan Daily Signal Recap sebagai pesan terpisah.

### Final Watchlist — 18:00

Final Watchlist hanya memprioritaskan:

- `BUY CONFIRMED` — maksimal 3;
- `BUY CANDIDATE` — maksimal 3;
- `WATCH HIGH` — maksimal 3 dan tanpa detail terpisah.

Detail terpisah dikirim untuk maksimal lima kandidat gabungan `BUY CONFIRMED` dan `BUY CANDIDATE`. Detail mencakup:

- technical quality, entry readiness, setup, RSI, dan volume;
- broker direction, confidence, net flow, buyer/seller concentration;
- Top Buyer 1–3 beserta nilai dan average price;
- Top Seller 1–3 beserta nilai dan average price;
- weighted average buyer, weighted average seller, harga terakhir, dan jarak harga terhadap buyer average;
- entry, TP, SL, risk/reward, atau trigger yang masih ditunggu.

## File Broker Raw

Nilai dan average price per broker dibaca dari file pendamping:

```text
BROKER_RAW_COMBINED_YYYY-MM-DD.csv
```

Tampermonkey mengekspor file ini bersama `BROKER_SUMMARY_COMBINED_YYYY-MM-DD.csv`. Sistem akan mencari raw file tanggal yang sama di folder Downloads dan menyalinnya otomatis ke:

```text
data/input/broker/BROKER_RAW_LATEST.csv
```

Jika raw file tidak ada, scoring tetap berjalan dari Broker Summary. Telegram hanya menampilkan nama broker dari summary dan memberi peringatan bahwa nilai/average per broker belum tersedia. Sistem tidak membuat angka palsu.

## BAT di Root

Root paket hanya berisi lima BAT harian:

```text
RUN_SDE.bat
RUN_MARKET_OUTLOOK.bat
RUN_POST_MARKET.bat
RUN_FINAL_WATCHLIST.bat
CHECK_SDE_STATUS.bat
```

`RUN_SDE.bat` adalah control panel untuk memilih Market Outlook, Post Market, Final Watchlist, full manual pipeline, status, test Telegram, atau maintenance.

Mode normal, preview, dan resend tersedia di dalam menu masing-masing BAT. Tidak ada lagi BAT terpisah untuk setiap variasi.

## Scheduler dan Maintenance

Task Scheduler memakai launcher non-interaktif:

```text
scheduler/SCHEDULE_MARKET_OUTLOOK.bat
scheduler/SCHEDULE_POST_MARKET.bat
scheduler/SCHEDULE_FINAL_WATCHLIST.bat
```

Utility teknis berada di `maintenance/`. Setelah extract atau memindahkan folder, buka `RUN_SDE.bat > Maintenance > Generate Task Scheduler XML`, lalu import XML baru dari:

```text
scheduler/windows/generated/
```

Jangan memakai XML dari versi atau folder lama karena XML menyimpan path absolut.

## Alur Harian

```text
07:30 Market Outlook
16:30 Post Market compact technical scan
18:00 Final Watchlist + broker detail
```

## Output Penting

```text
data/output/candidates/technical_candidates_top30.csv
data/input/broker/BROKER_SUMMARY_LATEST.csv
data/input/broker/BROKER_RAW_LATEST.csv
data/input/FINAL_DECISION_V2.csv
data/output/decision/FINAL_DECISION_V3.csv
data/output/exit/ENTRY_PLANS.csv
data/output/telegram_ui_preview/scheduled/
data/output/job_status/<trade-date>/
```

## Validasi

```bat
python -m unittest discover -s tests -p "test*.py" -v
python tools/verify_manifest.py
```

## Dokumentasi Aktif

- `CHANGELOG_SDE_SWING_V1_6_1_TELEGRAM_BROKER_DETAIL_CONTROL_PANEL.md`
- `SDE_V1_6_1_TELEGRAM_BAT_AUDIT.md`
- `CHANGELOG_SDE_SWING_V1_6_0_SIGNAL_QUALITY_ENTRY_READINESS.md`
- `docs/CARA_PAKAI_SDE_V1_6_1.md`
- `docs/WINDOWS_TASK_SCHEDULER_GUIDE_V2.md`
