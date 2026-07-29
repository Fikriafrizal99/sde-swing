# Database Deduplication Report

## Prinsip

Harga harian adalah canonical data. Data yang sama hanya disimpan satu kali berdasarkan:

```text
symbol + price_date + source
```

Setiap run menyimpan lineage:

```text
run_id + dataset_type + dataset_hash
```

## Regression

Test `test_database_deduplicates_market_prices` menjalankan archive harga yang sama dua kali dan memastikan jumlah row `market_prices_daily` tetap `1`.

## Dampak

- Database tidak membesar karena duplikasi teknis.
- Run tetap bisa diaudit melalui hash dataset.
- CSV operasional tetap dipertahankan.
- Archive harga memakai batch upsert agar tidak terlihat macet saat memproses ratusan file.
- File historical dengan hash yang sudah selesai diarchive dicatat di `archived_source_files` dan di-skip pada run berikutnya.
