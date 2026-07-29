# SDE Swing V1.3 Scheduler Hardening Audit

Tanggal audit: 2026-07-24

## Ringkasan

V1.3 memperkeras scheduler V1.2 supaya job harian tidak mencampur data beda tanggal, tidak mengirim watchlist final sebelum broker summary valid, dan tidak menganggap `--dry-run` sebagai sekadar preview lama.

Fokus hardening:

- Post Market dibuat technical-only.
- Final Watchlist wajib memakai technical snapshot dan broker summary pada trade date yang sama.
- Broker summary divalidasi penuh sebelum final decision.
- Telegram dibuat idempotent berdasarkan tipe laporan/tanggal dan detail sinyal per emiten.
- Scheduler Windows dapat dibuat ulang dengan XML valid tanpa placeholder.

## Temuan dan Perbaikan

| Area | Risiko V1.2 | Perbaikan V1.3 | File utama |
| --- | --- | --- | --- |
| Post Market | Bisa menjalankan broker fusion/final decision terlalu awal. | `post_market` hanya menjalankan tahap teknikal dan membuat technical snapshot. | `modules/job_runner/core.py`, `run_sde_job.py` |
| Validasi tanggal | Data teknikal dan broker bisa berbeda tanggal. | Final job menolak snapshot/broker summary yang bukan trade date job. | `modules/job_runner/core.py` |
| Dry run | `--dry-run` lebih mirip preview existing. | `--dry-run` menjalankan pipeline sesuai job tanpa kirim Telegram; perilaku lama dipindah ke `--preview-existing`. | `run_sde_job.py` |
| Broker summary | Readiness belum cukup ketat. | Validator cek file, schema, tanggal, sample data, duplikat, numeric field, dan coverage simbol. | `modules/job_runner/core.py` |
| Broker cutoff | Risiko retry tidak terkendali. | Cutoff default final watchlist 18:30 WIB dengan retry 5 menit. | `config/scheduler.json` |
| Missing broker | Final bisa tetap berjalan atau ambigu. | Default policy `skip_final_watchlist`; opsi `send_preliminary_watchlist` tersedia. | `config/scheduler.json`, `run_sde_job.py` |
| Locking | Job berbeda bisa berebut output yang sama. | Ditambahkan global resource lock untuk job penulis output. | `modules/job_runner/runtime.py`, `run_sde_job.py` |
| Telegram | Idempotency berbasis payload hash dapat mengirim ulang jika format berubah. | Main report idempotent per tanggal + report type; detail sinyal per tanggal + emiten + status + version. | `modules/job_runner/delivery.py` |
| Telegram panjang | Potensi `text[:4096]` memotong pesan. | Splitter pesan menjaga isi dan mengirim part per part. | `modules/job_runner/delivery.py` |
| HTML Telegram | Risiko karakter HTML merusak format. | Helper `html_escape` dipakai pada payload report. | `modules/job_runner/reports.py` |
| Task Scheduler XML | Placeholder dan encoding rawan gagal import. | Generator XML UTF-8 dengan path absolut dan validasi parse. | `generate_task_scheduler_xml.py` |
| Job status | Status belum cukup lengkap untuk diagnosis. | Status JSON kini memuat stage, snapshot, dependency, broker readiness, Telegram, lock, traceback. | `modules/job_runner/runtime.py` |

## Guardrail Tanggal

Final Watchlist hanya boleh lanjut bila tiga tanggal ini sama:

```text
job trade_date == technical snapshot trade_date == broker summary date
```

Jika salah satu tidak sama, job berhenti sebagai waiting/invalid data dan membuat data warning, bukan final watchlist.

## Guardrail Output Final

Final Watchlist tidak dibuat ketika:

- technical snapshot belum ada untuk trade date itu;
- broker summary belum ada;
- broker summary kosong atau schema salah;
- broker summary berisi sample/fixture/dummy;
- tanggal broker summary tidak sama dengan trade date;
- coverage simbol broker tidak cukup.

## Risiko Tersisa

- Live Post Market tetap membutuhkan dependency eksternal dari `requirements.txt`, terutama `yfinance` dan `requests`.
- Hari libur Bursa perlu dipelihara di `config/trading_calendar.json`.
- Live Telegram perlu diuji dengan token/chat ID milik user sebelum operasi penuh.
- XML generated memakai path absolut saat generator dijalankan; jika folder project dipindah, jalankan ulang generator.

