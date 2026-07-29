# Audit Output Telegram SDE Swing - 27 Juli 2026

## Ringkasan

Output berhasil dikirim ke Telegram, tetapi ditemukan inkonsistensi presentasi pada Final Watchlist dan Signal Detail. Masalah utama bukan kegagalan Telegram, melainkan formatter yang tidak membaca kontrak `Plan_Status=ACCEPT` dari Exit Engine serta UI yang menggabungkan pola broker dengan aggregate net flow.

## Temuan

### 1. Entry plan valid tampil WAITING

Exit Engine menggunakan status `ACCEPT` untuk plan yang lolos. Formatter sebelumnya tidak memasukkan status tersebut ke daftar status valid. Ini menyebabkan level entry, TP, dan SL disembunyikan walaupun tersedia di `ENTRY_PLANS.csv`.

### 2. Pola broker dan net flow terlihat bertentangan

Contoh:

- TKIM: `STRONG ACCUMULATION`, net flow negatif.
- AKRA: `DISTRIBUTION`, net flow positif.

Kondisi ini dapat terjadi karena `Broker_Confirmation` memakai kombinasi Broker Score, concentration, dan ACCDIST, sedangkan `NET_FLOW` hanya satu komponen aggregate. UI baru tidak mengubah klasifikasi, tetapi memberi label `DIVERGENCE` agar pengguna memahami bahwa sinyalnya campuran.

### 3. Ranking tidak sesuai tampilan

Status internal `WATCH` dan `SPECULATIVE` sama-sama dipetakan menjadi `WATCH`, tetapi ranking lama tetap membedakannya. Ranking baru memakai status publik sehingga urutan terlihat konsisten dengan score dan readiness yang ditampilkan.

### 4. Judul prioritas menyesatkan

Jika semua kandidat `WAITING` atau `NOT READY`, judul baru menjadi `PANTAUAN UTAMA — belum ada setup READY`.

### 5. Format alasan berulang

Net flow angka mentah dihapus dari alasan karena sudah tersedia pada field `Net Flow` dengan format rupiah Indonesia.

### 6. Urutan dan spasi pesan

Setiap delivery sekarang memiliki `delivery_sequence` dan `delivery_total`. Teks juga dinormalisasi untuk mencegah jeda kosong tiga baris atau lebih.

## Kesimpulan

Output 27 Juli 2026 membuktikan delivery Telegram berjalan, tetapi versi V1.5.3 masih memiliki defect presentasi. Semua temuan di atas diperbaiki pada V1.5.4 tanpa mengubah keputusan inti mesin.
