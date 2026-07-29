# Exit Engine — SDE Swing V1.6

Baseline aktif:

- plan dibuat untuk STRONG BUY, BUY, BUY CANDIDATE, WATCH HIGH, dan WATCH berkualitas tertentu;
- broker direction distribution menahan pembuatan plan;
- minimum RR default 1R dan preferred RR 2R;
- maksimum risiko stop default 7%;
- risk dan target dihitung dari `Entry_Reference_Price`, bukan otomatis dari current close;
- resistance minor dapat menghasilkan `CONDITIONAL` bila jalur resistance mayor masih valid;
- `ACCEPT` berarti READY, `CONDITIONAL` berarti menunggu trigger/area entry, dan `REJECT` berarti guardrail gagal;
- market regime BEAR/BEARISH menolak entry baru;
- downgrade keputusan ke AVOID/SPECULATIVE menutup trade aktif;
- bila stop dan target tersentuh pada candle yang sama, stop diprioritaskan secara konservatif.

Output utama:

```text
ENTRY_PLANS.csv
APPROVED_ENTRIES.csv
CONDITIONAL_ENTRIES.csv
REJECTED_ENTRIES.csv
EXIT_ALERTS.csv
```
