# Global Market Yahoo Test Report

Tanggal uji: 2026-07-24

## Unit Test

Command:

```bat
python -m unittest tests.test_global_market -v
```

Hasil:

```text
9 tests OK
```

Skenario yang diuji:

- registry hanya menerima provider `YAHOO`;
- batch fetch sukses;
- satu simbol gagal tidak menggagalkan Market Outlook;
- semua simbol gagal menghasilkan `INSUFFICIENT_DATA`;
- Yahoo mengembalikan data kosong;
- close kosong ditolak;
- previous close kosong ditolak;
- data stale ditolak;
- cache valid digunakan;
- cache stale tidak digunakan;
- retry berhasil;
- retry gagal tercatat;
- tidak ada fallback provider;
- `is_fallback` selalu false;
- sentiment `RISK_ON`;
- sentiment `RISK_OFF`;
- sentiment `NEUTRAL`;
- sentiment `INSUFFICIENT_DATA`;
- snapshot tersimpan;
- snapshot ID tercatat pada job status;
- dry-run tidak mengirim Telegram.

## Full Regression

Command:

```bat
python -m unittest discover -s tests -p "test*.py" -v
```

Hasil:

```text
57 tests OK
```

## Live Yahoo Dry Run

Dependency diuji dari folder kerja sementara:

```text
work/global_market_pydeps
```

Command:

```bat
python run_sde_job.py --job market_outlook --dry-run --trade-date 2026-07-24 --force
```

Hasil live Yahoo:

```text
exit code 0
status SUCCESS
provider YAHOO
source_mode LIVE
coverage 100%
global sentiment NEUTRAL
Telegram SKIPPED
```

Simbol live tervalidasi:

```text
^GSPC
^IXIC
^DJI
^VIX
^N225
^HSI
000001.SS
^KS11
IDR=X
DX-Y.NYB
GC=F
CL=F
BZ=F
NG=F
```

Catatan penting:

- Validator memilih latest valid closed row, bukan baris harian kosong/parsial dari Yahoo.
- Tidak ada angka dummy.
- Tidak ada sample data.
- Tidak ada fallback provider.
- Jika `yfinance` belum terpasang, Market Outlook tetap dibuat dengan `DATA_NOT_AVAILABLE`.

