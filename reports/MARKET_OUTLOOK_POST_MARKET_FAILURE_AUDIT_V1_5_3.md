# Audit Market Outlook dan Post Market - SDE Swing V1.5.3

## Ringkasan

Audit dilakukan terhadap alur BAT, job runner, report builder, delivery Telegram, snapshot Global Market, snapshot teknikal, status JSON, dan idempotency.

## Temuan Market Outlook

### MO-01 - Delivery gagal tetapi exit code sukses

Sebelum perbaikan, `deliver()` dapat menghasilkan status `FAILED`, tetapi `job_market_outlook()` tetap menyelesaikan job dengan exit code `0`. BAT kemudian menampilkan `MARKET OUTLOOK selesai` dan langsung keluar.

Perbaikan: delivery gagal sekarang menghasilkan `DELIVERY_FAILED` dengan exit code `50`.

### MO-02 - Snapshot valid dapat tertimpa refresh gagal

Refresh Yahoo dengan coverage nol tetap menulis `global_market_snapshot.json`. Ini dapat menghilangkan snapshot valid yang sebelumnya sudah tersedia.

Perbaikan: snapshot utama hanya diperbarui jika coverage memenuhi `minimum_sentiment_coverage_ratio`. Percobaan refresh gagal disimpan sebagai file audit terpisah. Snapshot valid existing dipakai sebagai fallback.

### MO-03 - Data global invalid tetap dianggap sukses

Coverage nol dan seluruh instrumen gagal sebelumnya tetap menghasilkan status `SUCCESS`.

Perbaikan: pengiriman dibatalkan dengan status `INVALID_GLOBAL_MARKET_DATA` bila coverage tidak memenuhi guardrail dan fallback valid tidak tersedia.

## Temuan Post Market

### PM-01 - Tidak ada fallback ketika refresh Yahoo saham gagal

Sebelum perbaikan, kegagalan downloader/IHSG/technical stage menghentikan seluruh Post Market walaupun snapshot teknikal current-date tersedia.

Perbaikan: `fallback_to_existing_on_refresh_failure=true` memakai snapshot existing tanggal yang sama dan menambahkan Data Warning.

### PM-02 - Preview existing tidak benar-benar memuat snapshot

Mode `--preview-existing` sebelumnya melewatkan technical stage tetapi meneruskan manifest kosong. Report builder kemudian membaca file root secara implisit tanpa validasi snapshot tanggal.

Perbaikan: mode existing memuat `latest_snapshot.json`, memeriksa status `VALID`, lalu membangun manifest eksplisit dari snapshot tersebut.

### PM-03 - Delivery gagal tetap exit code sukses

Closing Bell dan Daily Signal Recap dapat sama-sama gagal dikirim, tetapi exit code tetap `0`.

Perbaikan: kegagalan satu atau lebih payload menghasilkan exit code `50` dan rincian error tetap dicatat per report.

## Perubahan BAT

`RUN_SDE_MARKET_OUTLOOK.bat` dan `RUN_SDE_POST_MARKET.bat` kini:

- menampilkan progres job;
- mencetak status JSON terbaru;
- menampilkan status delivery setiap report;
- menampilkan error Telegram;
- mempertahankan jendela selama 20 detik pada kegagalan.

## Hasil Pengujian

```text
Python compile                 PASS
Unit/regression tests          69 PASS
Market delivery failure       PASS -> exit 50
Post Market delivery failure  PASS -> exit 50
Global snapshot fallback      PASS
Snapshot overwrite guardrail  PASS
```

## Batas Pengujian

Paket ZIP audit tidak menyertakan bot token dan chat ID. Karena itu, live send ke Telegram asli tidak dilakukan. Jalur pengiriman diuji dengan mock Telegram response, sedangkan kegagalan konfigurasi diuji melalui status dan exit code.
