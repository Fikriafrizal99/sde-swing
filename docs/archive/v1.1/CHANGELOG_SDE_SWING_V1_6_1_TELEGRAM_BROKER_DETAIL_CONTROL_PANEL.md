# Changelog — SDE Swing V1.6.1 Telegram Broker Detail & Control Panel

Pipeline ID: `SDE_SWING_V1_6_1_TELEGRAM_BROKER_DETAIL_CONTROL_PANEL`

## Scope

- Market Outlook tidak diubah.
- Post Market dipadatkan menjadi satu laporan teknikal.
- Final Watchlist diperkaya dengan rincian Broker Raw.
- BAT root disederhanakan dan dipisahkan dari scheduler/maintenance.

## Telegram

### Post Market

- Menghapus pengiriman Closing Bell dan Daily Signal Recap sebagai pesan terpisah.
- Menambahkan satu ringkasan berisi market condition, jumlah saham diproses, kandidat, Ready Zone, Developing, Extended, dan top technical candidates.
- Tidak menampilkan entry/TP/SL atau keputusan broker sebelum final stage.

### Final Watchlist

- Ringkasan hanya menampilkan BUY CONFIRMED, BUY CANDIDATE, dan WATCH HIGH.
- Batas default 3 kandidat per kategori.
- Detail terpisah hanya untuk maksimal 5 BUY CONFIRMED/BUY CANDIDATE terbaik.
- Menambahkan Top Buyer/Top Seller 1–3 dari Broker Raw.
- Menambahkan NET_VALUE dan AVG_PRICE tiap broker.
- Menambahkan weighted average buyer/seller, current price, dan distance to weighted buyer average.
- Jika Broker Raw tidak tersedia, fallback ke nama broker dari summary dan warning eksplisit; tidak membuat nilai atau average palsu.

## Broker Data Flow

- Menambahkan path `broker_raw_latest`.
- Broker bridge menerima `--raw-output`.
- Companion raw file tanggal yang sama divalidasi dan disalin ke controlled input serta archive.
- Final-watchlist job juga dapat menemukan raw file langsung dari Downloads jika bridge belum menyalinnya.

## BAT

Root hanya menyisakan:

- `RUN_SDE.bat`
- `RUN_MARKET_OUTLOOK.bat`
- `RUN_POST_MARKET.bat`
- `RUN_FINAL_WATCHLIST.bat`
- `CHECK_SDE_STATUS.bat`

Mode normal, preview, existing data, resend, status, Telegram test, dan maintenance tersedia melalui menu.

Task Scheduler memakai:

- `scheduler/SCHEDULE_MARKET_OUTLOOK.bat`
- `scheduler/SCHEDULE_POST_MARKET.bat`
- `scheduler/SCHEDULE_FINAL_WATCHLIST.bat`

Utility teknis dipindahkan ke `maintenance/`.

## Guardrail

- Market Outlook formatter dan payload tidak diubah.
- Broker Raw tidak memengaruhi scoring; hanya memperkaya presentasi dan audit.
- Date validation wajib sama dengan trade date.
- Dry-run dapat membaca raw tetapi tidak menyalin ke controlled input.
- Detail broker hanya dikirim untuk kandidat prioritas, sehingga Telegram tidak kembali penuh.
