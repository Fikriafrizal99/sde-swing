# Windows Task Scheduler Guide — SDE Swing V1.6.1

## Jadwal Normal

```text
07:30  Market Outlook
16:30  Post Market compact technical scan
18:00  Final Watchlist + broker detail
```

Final Watchlist menunggu Broker Summary sampai cutoff konfigurasi, default 18:30 WIB.

## Launcher Scheduler

Task Scheduler harus menjalankan launcher non-interaktif berikut:

```text
scheduler/SCHEDULE_MARKET_OUTLOOK.bat
scheduler/SCHEDULE_POST_MARKET.bat
scheduler/SCHEDULE_FINAL_WATCHLIST.bat
```

Jangan arahkan Task Scheduler ke `RUN_SDE.bat` atau BAT menu di root.

## Generate XML

Setelah extract atau memindahkan folder:

1. Klik `RUN_SDE.bat`.
2. Pilih `Maintenance`.
3. Pilih `Generate Task Scheduler XML`.

Alternatif command line:

```bat
python generate_task_scheduler_xml.py
```

XML baru tersimpan di:

```text
scheduler/windows/generated/
```

File:

```text
SDE_MARKET_OUTLOOK.xml
SDE_POST_MARKET.xml
SDE_FINAL_WATCHLIST.xml
```

XML menyimpan path absolut. Jangan menggunakan XML dari folder atau versi lama.

## Import

1. Buka Windows Task Scheduler.
2. Pilih `Import Task`.
3. Pilih XML dari `scheduler/windows/generated/`.
4. Cek tab Actions.
5. Pastikan Command mengarah ke launcher dalam folder `scheduler`.
6. Pastikan Working Directory mengarah ke root project.
7. Simpan dan jalankan `Run` untuk pengujian.

## Pengaturan Penting

```text
Multiple instances: Do not start a new instance
Run whether user is logged on or not
Start when available
Wake the computer to run this task
```

Stop limit:

```text
Market Outlook: 30 menit
Post Market: 60 menit
Final Watchlist: 60 menit
```

## Validasi Manual

Dari root:

```text
RUN_MARKET_OUTLOOK.bat
RUN_POST_MARKET.bat
RUN_FINAL_WATCHLIST.bat
CHECK_SDE_STATUS.bat
```

Setiap BAT menyediakan mode normal, preview, atau resend di dalam menu.
