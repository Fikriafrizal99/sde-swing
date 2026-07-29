# Yahoo Refresh Implementation Report

## Valid Closed Candle

Downloader membedakan:

- `VALID_CLOSED_CANDLE`
- `PARTIAL_DAILY_CANDLE`
- `NO_NEW_VALID_CANDLE`
- `PROVIDER_FAILED`
- `LAST_VALID_USED`

Closed candle valid wajib memiliki:

- `Date`
- `Open`
- `High`
- `Low`
- `Close`
- `Volume`

Jika `Close` kosong, simbol mendapat status `PARTIAL_CANDLE_IGNORED` dan tidak dihitung sebagai `UPDATED_VALID`.

## Policy

Default:

```text
DAILY_CANDLE_POLICY=LAST_CLOSED_CANDLE
ALLOW_PARTIAL_DAILY_CANDLE=false
YAHOO_FAILURE_POLICY=STOP
YAHOO_INCREMENTAL_OVERLAP_DAYS=5
YAHOO_BATCH_ENABLED=true
YAHOO_BATCH_SIZE=50
YAHOO_MAX_WORKERS=4
YAHOO_REQUEST_DELAY_SECONDS=1
YAHOO_MAX_RETRIES=3
```

Mode interaktif dapat memilih retry, gunakan last valid, pilih file manual, atau batalkan pipeline.

## Optimasi Incremental

Yahoo refresh sekarang melakukan local pre-check sebelum request provider.
Setiap simbol diklasifikasikan ke salah satu action:

- `FULL_BACKFILL` jika file belum ada, kosong, schema rusak, tidak memiliki valid candle, atau operator menjalankan `--full-backfill`.
- `INCREMENTAL_UPDATE` jika file lokal tertinggal dari latest expected closed candle.
- `SKIP_ALREADY_CURRENT` jika latest valid closed candle lokal sudah current.

Default pipeline adalah incremental + skip. Simbol current tidak melakukan
network request. `--force-refresh` mengabaikan skip tetapi tetap memakai
incremental overlap; `--full-backfill` baru menarik ulang full range.

Incremental update memakai safety overlap kecil. Contoh, local latest
`2026-07-20` dengan overlap 5 hari akan meminta ulang mulai `2026-07-15`,
kemudian hasil digabung, diurutkan, dan deduplicate berdasarkan `Symbol` +
`Date`.

## Batch Processing

Jika `YAHOO_BATCH_ENABLED=true`, simbol yang perlu update dikelompokkan per
window download dan dikirim ke Yahoo dalam batch. Jika batch gagal, downloader
retry batch, memecah batch menjadi kelompok lebih kecil, lalu fallback ke
request individual hanya untuk simbol bermasalah.

Console dibuat ringkas:

```text
Yahoo Refresh - INCREMENTAL MODE
Universe             : 445 simbol
Already current      : 430
Need incremental     : 10
Need full backfill   : 1
Retry/new/invalid    : 4
```

## Manifest

Per run:

- `data/output/manifests/YAHOO_REFRESH_MANIFEST_<RUN_ID>.json`
- `data/output/manifests/YAHOO_SYMBOL_STATUS_<RUN_ID>.csv`

Field live/source penting:

- `Provider_Mode = LIVE` atau `FIXTURE`
- `Data_Source = LIVE_YAHOO` atau `OFFLINE_FIXTURE`
- `Mode = PRODUCTION` atau `TEST`
- `Network_Request_Performed`
- `Live_Response_Received`
- `Refresh_Status`
- `Candle_Status`
- `Refresh_Mode`
- `Full_Backfill_Count`
- `Incremental_Update_Count`
- `Already_Current_Count`
- `Skipped_Count`
- `Network_Request_Symbol_Count`
- `Network_Request_Batch_Count`
- `Retry_Count`
- `Started_At`
- `Finished_At`
- `Duration_Seconds`

Jika live Yahoo berhasil, manifest mencatat `Provider_Mode=LIVE`,
`Network_Request_Performed=true`, `Live_Response_Received=true`, dan
`Refresh_Status=SUCCESS`.

Fixture tidak pernah ditampilkan sebagai live data.
