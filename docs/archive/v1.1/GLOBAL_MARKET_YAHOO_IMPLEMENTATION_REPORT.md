# Global Market Yahoo Implementation Report

Tanggal implementasi: 2026-07-24

## Ringkasan

Market Outlook sekarang memakai Yahoo Finance sebagai satu-satunya provider global market. Tidak ada Alpha Vantage, Twelve Data, FRED, scraping website lain, atau API key baru.

Implementasi ini menambahkan:

- registry simbol global terpusat di `config/global_market.json`;
- provider reusable `modules/global_market/yahoo_global_market_provider.py`;
- validator freshness/price/date;
- cache ringan dengan guardrail anti-stale;
- global market snapshot;
- global sentiment scoring;
- integrasi ke `market_outlook`;
- status job dengan global snapshot ID.

## File Baru

```text
config/global_market.json
modules/global_market/__init__.py
modules/global_market/global_market_registry.py
modules/global_market/yahoo_global_market_provider.py
modules/global_market/global_market_validator.py
modules/global_market/global_market_snapshot.py
modules/global_market/global_market_scoring.py
tests/test_global_market.py
docs/GLOBAL_MARKET_YAHOO_IMPLEMENTATION_REPORT.md
docs/GLOBAL_MARKET_YAHOO_TEST_REPORT.md
docs/CARA_PAKAI_SDE_V1_4.md
```

## Simbol Yahoo

Registry aktif:

```text
S&P 500              ^GSPC
Nasdaq               ^IXIC
Dow Jones            ^DJI
VIX                  ^VIX
Nikkei 225           ^N225
Hang Seng            ^HSI
Shanghai Composite   000001.SS
KOSPI                ^KS11
USD/IDR              IDR=X
Dollar Index         DX-Y.NYB
Gold                 GC=F
WTI Crude Oil        CL=F
Brent Crude Oil      BZ=F
Natural Gas          NG=F
```

Coal dan CPO tidak ditambahkan karena prompt meminta tidak memaksakan instrumen jika simbol Yahoo tidak stabil/tervalidasi.

## Snapshot

Snapshot disimpan di:

```text
data/output/global_market/<trade-date>/global_market_snapshot.json
data/output/global_market/<trade-date>/GLOBAL-MARKET-YYYYMMDD-HHMMSS.json
```

Snapshot berisi:

- snapshot ID;
- job run ID;
- trade date Indonesia;
- provider `YAHOO`;
- source mode `LIVE`;
- daftar instrumen;
- market date;
- close dan previous close;
- change point dan change percentage;
- freshness status;
- coverage ratio;
- global sentiment;
- warnings dan errors;
- cache summary.

## Freshness

Validator tidak mengambil baris Yahoo terbaru secara buta. Untuk menghindari candle harian parsial, validator memilih latest valid close yang tidak melewati expected completed session per kategori market.

Status yang digunakan:

```text
VALID
DELAYED_ACCEPTED
STALE
DATA_NOT_AVAILABLE
FETCH_FAILED
INVALID_PRICE
INVALID_DATE
```

Jika satu instrumen gagal, Market Outlook tetap dibuat dan instrumen tersebut tampil `DATA_NOT_AVAILABLE`.

## Cache dan Retry

Konfigurasi default:

```text
retry_count: 2
retry_delay_seconds: 3
request_timeout_seconds: 20
cache_enabled: true
cache_max_age_minutes: 30
```

Cache hanya digunakan jika setelah divalidasi masih `VALID` atau `DELAYED_ACCEPTED`. Cache stale tidak dipakai sebagai data valid.

## Global Sentiment

Klasifikasi:

```text
RISK_ON
NEUTRAL
RISK_OFF
INSUFFICIENT_DATA
```

Global sentiment dipakai hanya untuk:

- Market Outlook;
- konteks market;
- arahan global.

Global sentiment belum mengubah scoring saham, final decision, broker fusion, decision engine, atau exit engine.

## Integrasi Job

`market_outlook` sekarang berjalan:

```text
validasi trading day
load global market registry
fetch Yahoo global market
validasi price/date/freshness
simpan global market snapshot
hitung global sentiment
generate Market Outlook
simpan preview
kirim Telegram jika bukan dry-run
```

Status job mencatat:

```text
global_market_snapshot_id
global_market_coverage_ratio
global_sentiment_state
global_sentiment_score
provider_status
data_source_mode
```

