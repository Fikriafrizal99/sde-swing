# Changelog - SDE Swing V1.5.4 Telegram Consistency Fix

Pipeline version: `SDE_SWING_V1_5_4_TELEGRAM_CONSISTENCY_FIX`

## Masalah yang Ditemukan dari Output 27 Juli 2026

1. Entry plan valid dari Exit Engine memakai `Plan_Status=ACCEPT`, tetapi formatter Telegram hanya mengenali `APPROVED`, `VALID`, `READY`, dan `ACTIVE`. Akibatnya entry, TP, dan SL yang sebenarnya tersedia tampil sebagai `WAITING`.
2. `Broker_Confirmation` dan `NET_FLOW` ditampilkan dalam satu label `Broker Flow`, sehingga kondisi seperti `STRONG ACCUMULATION` dengan net flow negatif atau `DISTRIBUTION` dengan net flow positif terlihat seperti kontradiksi tanpa penjelasan.
3. Ranking memakai status internal `WATCH` dan `SPECULATIVE`, sementara keduanya sama-sama ditampilkan sebagai `WATCH`. Hasilnya urutan score terlihat tidak konsisten bagi pengguna.
4. Judul `PRIORITAS UTAMA` tetap digunakan walaupun tidak ada setup yang READY.
5. `Decision_Reasons` mengulang net flow dalam angka mentah seperti `21,887,450,300` walaupun nilai yang sama sudah ditampilkan pada bagian flow.
6. Delivery log belum mencatat nomor urut payload dan teks belum dinormalisasi dari jeda kosong berlebihan.

## Perbaikan

- `Plan_Status=ACCEPT` dan `ACCEPTED` sekarang dikenali sebagai `ENTRY READINESS: READY` jika level entry, target, dan stop lengkap.
- Watchlist dan detail memisahkan:
  - `Broker Pattern`
  - `Net Flow`
  - `Flow Status`
- Ketidaksesuaian arah pola broker dan net flow diberi status `DIVERGENCE` tanpa mengubah keputusan atau scoring mesin.
- Ranking menggunakan status publik yang benar-benar terlihat di Telegram, lalu readiness, kualitas flow, dan score.
- Jika tidak ada plan READY, judul berubah menjadi `PANTAUAN UTAMA — belum ada setup READY` dan arahan menegaskan bahwa urutan bukan prioritas beli.
- Net flow mentah dihapus dari alasan agar tidak berulang dan seluruh nominal tetap memakai format Indonesia.
- Teks Telegram dinormalisasi agar maksimal dua baris kosong berturut-turut.
- Delivery log mencatat `delivery_sequence` dan `delivery_total` untuk audit urutan pesan.

## Dampak terhadap Mesin Keputusan

Perubahan ini tidak mengubah Technical Score, Broker Score, Broker Fusion, FINAL_DECISION_V3, Entry Plan Engine, atau Exit Engine. Perubahan hanya memperbaiki pembacaan status plan, ranking presentasi, format Telegram, dan audit delivery.

## Validasi

- Seluruh unit dan regression test: PASS.
- Test khusus `Plan_Status=ACCEPT`: PASS.
- Test broker divergence positif/negatif: PASS.
- Test ranking status publik: PASS.
- Test normalisasi spasi dan urutan delivery: PASS.
