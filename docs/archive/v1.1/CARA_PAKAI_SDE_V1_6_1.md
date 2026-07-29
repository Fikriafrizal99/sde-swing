# Cara Pakai SDE Swing V1.6.1

## 1. Control Panel

Klik `RUN_SDE.bat`. Menu ini menjadi pintu utama untuk:

1. Market Outlook;
2. Post Market;
3. Final Watchlist;
4. Full Manual Pipeline;
5. cek status;
6. test Telegram;
7. maintenance.

## 2. Market Outlook

Format Market Outlook tidak berubah. Pilih:

- Normal: refresh dan kirim;
- Preview existing: tidak mengirim;
- Kirim ulang existing: force resend.

## 3. Post Market

Post Market mengirim satu ringkasan teknikal. Tidak ada entry, TP, SL, atau detail broker karena keputusan final belum dibuat.

Pilih:

- Normal: refresh historical/technical dan kirim;
- Preview existing: memakai snapshot existing tanpa kirim;
- Kirim ulang existing: memakai snapshot existing dan force resend.

## 4. Broker Summary dan Broker Raw

Setelah kandidat teknikal tersedia, ekspor Broker Summary melalui Tampermonkey. Pastikan dua file tanggal yang sama tersedia:

```text
BROKER_SUMMARY_COMBINED_YYYY-MM-DD.csv
BROKER_RAW_COMBINED_YYYY-MM-DD.csv
```

`BROKER_SUMMARY` dipakai untuk scoring dan fusion. `BROKER_RAW` dipakai untuk menampilkan rincian Top Buyer/Top Seller, nilai transaksi, dan average price tiap broker.

Sistem mencari keduanya dari folder Downloads. Raw file valid disalin ke:

```text
data/input/broker/BROKER_RAW_LATEST.csv
```

## 5. Final Watchlist

Final Watchlist mengirim:

1. satu ringkasan kandidat;
2. detail terpisah untuk maksimal lima `BUY CONFIRMED`/`BUY CANDIDATE` terbaik.

Detail broker berisi:

- status dan confidence;
- net flow dan concentration;
- Top Buyer 1–3: kode broker, nilai, average price;
- Top Seller 1–3: kode broker, nilai, average price;
- weighted average buyer/seller;
- jarak harga terakhir dari average buyer.

Jika raw file tidak ditemukan, laporan tetap dikirim tetapi nilai dan average per broker ditandai belum tersedia.

## 6. BAT Harian

Root hanya memiliki:

```text
RUN_SDE.bat
RUN_MARKET_OUTLOOK.bat
RUN_POST_MARKET.bat
RUN_FINAL_WATCHLIST.bat
CHECK_SDE_STATUS.bat
```

Launcher otomatis berada di `scheduler/`. Utility teknis berada di `maintenance/`.

## 7. Task Scheduler

Dari control panel pilih Maintenance > Generate Task Scheduler XML. Import file baru dari:

```text
scheduler/windows/generated/
```

Task Scheduler menjalankan launcher non-interaktif di folder `scheduler`, bukan menu BAT di root.

## 8. Urutan Normal

```text
07:30 Market Outlook
16:30 Post Market
18:00 Final Watchlist
```

Final Watchlist dapat menunggu Broker Summary sampai cutoff yang dikonfigurasi.
