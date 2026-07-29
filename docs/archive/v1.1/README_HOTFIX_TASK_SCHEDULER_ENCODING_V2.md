# SDE Swing V1.6.2 — Task Scheduler Encoding Hotfix V2

## Masalah

Task Scheduler menolak XML dengan pesan:

```text
The format of the task is not valid.
(1,40): ERROR: unable to switch the encoding
```

XML sebelumnya ditulis sebagai UTF-8. Pada Windows yang mengalami error ini, Task Scheduler membutuhkan XML UTF-16 dengan BOM, seperti format XML yang diekspor langsung oleh Task Scheduler.

## Perbaikan

- Generator menulis XML sebagai UTF-16 dengan BOM.
- Deklarasi XML menjadi `encoding='utf-16'`.
- Validasi generator memastikan BOM dan deklarasi encoding cocok.
- Struktur trigger dari hotfix sebelumnya tetap dipertahankan.

## Instalasi

1. Copy isi patch ke root SDE V1.6.2 dan overwrite.
2. Hapus semua XML di `scheduler\windows\generated`.
3. Jalankan `maintenance\GENERATE_SCHEDULER_XML.bat`.
4. Import ulang XML yang baru.

Jangan mengubah atau menyimpan ulang XML menggunakan editor sebelum import.
