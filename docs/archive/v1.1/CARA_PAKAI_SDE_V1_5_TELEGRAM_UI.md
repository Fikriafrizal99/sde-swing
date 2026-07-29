# Cara Pakai SDE Swing V1.5 Professional Telegram UI

## 1. Konfigurasi

Buka `config/telegram.json`.

Isi:

```json
{
  "telegram": {
    "bot_token": "TOKEN_BOT",
    "chat_id": "CHAT_ID"
  }
}
```

Topic ID tetap dikendalikan secara terpusat melalui `config/scheduler.json` pada bagian:

```text
delivery.topic_routing
```

Jangan hardcode topic ID di formatter atau file job.

Jika manual test Python berhasil tetapi BAT seperti memakai Python lain, set override ini dari CMD sebelum menjalankan BAT:

```bat
set "SDE_PYTHON=C:\Path\Ke\python.exe"
RUN_SDE_POST_MARKET_CEK.bat
```

Semua BAT akan membaca `SDE_PYTHON` lebih dulu. Jika kosong, BAT mencoba `python`, lalu fallback ke `py -3`.

## 2. Preview UI

Contoh tujuh laporan tersedia di:

```text
data/output/telegram_ui_preview/scheduled/
```

File preview memakai HTML Telegram. Tag seperti `<b>` dan `<pre>` akan dirender oleh Telegram saat dikirim.

## 3. Uji Scheduler Tanpa Mengirim Telegram

### Market Outlook

```bat
python run_sde_job.py --job market_outlook --preview-existing --dry-run --trade-date 2026-07-24 --force
```

### Closing Bell dan Rekap 16:30

```bat
python run_sde_job.py --job post_market --preview-existing --dry-run --trade-date 2026-07-24 --force
```

### Final Watchlist

```bat
python run_sde_job.py --job final_watchlist --dry-run --trade-date 2026-07-24 --force
```

Final Watchlist hanya dibuat jika technical snapshot dan broker summary lulus guardrail tanggal, schema, coverage, duplicate, numeric, dan sample-data validation.

## 4. Menjalankan Otomatis

Gunakan Windows Task Scheduler XML yang sudah tersedia atau launcher berikut:

```text
RUN_SDE_MARKET_OUTLOOK.bat
RUN_SDE_POST_MARKET.bat
RUN_SDE_FINAL_WATCHLIST.bat
```

Catatan:

BAT scheduler dibuat untuk Task Scheduler, sehingga kalau diklik dari Explorer jendelanya bisa langsung menutup setelah job selesai/gagal/skip. Untuk cek manual, gunakan:

```text
RUN_SDE_MARKET_OUTLOOK_CEK.bat
RUN_SDE_POST_MARKET_CEK.bat
RUN_SDE_FINAL_WATCHLIST_CEK.bat
RUN_SDE_CEK_STATUS_SEMUA.bat
```

File cek manual memakai `--dry-run --force`, tidak mengirim Telegram, lalu menampilkan status, status delivery, lokasi preview, dan alasan seperti `TECHNICAL_SNAPSHOT_NOT_FOUND`, `DATE_MISMATCH`, `WAITING_DATA_TIMEOUT`, dependency belum terpasang, atau `SKIPPED_NON_TRADING_DAY`.

Jika laporan sudah pernah dikirim dari test manual Python, BAT scheduler bisa tidak mengirim ulang karena idempotency. Untuk sengaja kirim ulang, gunakan:

```text
RUN_SDE_MARKET_OUTLOOK_KIRIM_ULANG.bat
RUN_SDE_POST_MARKET_KIRIM_ULANG.bat
```

Kedua launcher ini meminta konfirmasi `Y/N`, memakai `--force`, lalu menampilkan status setelah proses selesai. `RUN_SDE_POST_MARKET_KIRIM_ULANG.bat` memakai `--preview-existing`, jadi cocok saat data sudah ada dan tidak ingin refresh Yahoo saham.

Jika folder hasil copy masih menampilkan path lama, JSON Yahoo lama, atau status dari folder sebelumnya, jalankan:

```text
RUN_SDE_RESET_STATUS_RUNTIME.bat
```

Reset ini hanya menghapus status/log/cache/preview/manifest runtime sementara, termasuk JSON Yahoo lama. File data utama tidak dihapus.

Jadwal default:

```text
07:30 WIB  Market Outlook
16:30 WIB  Post Market
18:00 WIB  Final Watchlist
```

Jika ingin pakai Task Scheduler setelah folder dicopy/extract, jalankan dulu:

```text
GENERATE_TASK_SCHEDULER_XML.bat
```

Setelah itu import XML baru dari `scheduler/windows/generated/`. XML generated menyimpan path absolut, jadi jangan pakai XML dari folder lama.

## 5. Arti Status Tampilan

```text
🟢 BUY CONFIRMED = keputusan mesin STRONG BUY
🟡 WATCH HIGH    = keputusan mesin BUY
🔵 WATCH         = keputusan mesin WATCH atau SPECULATIVE
🔴 AVOID         = keputusan mesin AVOID
```

`ENTRY READINESS` adalah status rencana eksekusi, bukan keputusan baru.

```text
READY     = entry plan lolos guardrail
WAITING   = level valid belum tersedia
NOT READY = entry plan ditolak guardrail
```

Saham dapat memiliki keputusan `BUY CONFIRMED` tetapi `ENTRY READINESS: NOT READY`. Artinya kualitas kandidat tinggi, namun entry saat ini belum layak karena risk reward atau guardrail lain belum terpenuhi.

## 6. Data Warning

Data Warning selalu menjelaskan:

- status teknis;
- kondisi data dalam bahasa manusia;
- dampaknya terhadap keputusan;
- larangan menggunakan hasil sebagai dasar entry sampai data valid.

## 7. Test

Jalankan:

```bat
python -m unittest discover -s tests -p "test*.py" -v
```

Lalu verifikasi checksum:

```bat
RUN_VERIFY_MANIFEST.bat
```
