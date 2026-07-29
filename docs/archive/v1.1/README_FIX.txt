SDE v4.3 - TELEGRAM FRESHNESS FIX

ROOT CAUSE
1. Telegram does not use cached messages. It reads data/output/decision/FINAL_DECISION_V3.csv directly.
2. The technical source can be current while broker snapshot remains older.
3. If rankings and classifications do not cross thresholds, the same symbols can legitimately remain STRONG BUY/BUY/WATCH.
4. The old Telegram report did not display source dates or current Close, making fresh output look identical to yesterday.

INSTALL
Copy this file into the project root and replace:
modules/telegram/telegram_bot.py

NEW OUTPUT
- Prints exact decision source path used by Telegram.
- Prints row count, technical date, broker date and decision counts in pipeline console.
- Telegram message displays Technical data date and Broker data date.
- Each STRONG BUY/BUY item displays current Close and both dates.
- Shows a stale broker warning when broker snapshot is behind technical data.

IMPORTANT
This fix does not artificially change a BUY/SELL decision merely to make the report look different. If the same stocks remain above the thresholds, the recommendation can remain the same while price, score and date are newer.
