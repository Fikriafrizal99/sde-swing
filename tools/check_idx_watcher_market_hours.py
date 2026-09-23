#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")
START = time(7, 0)
END = time(18, 0)


def main() -> int:
    now = datetime.now(WIB)
    clock = now.timetz().replace(tzinfo=None)
    allowed = now.weekday() < 5 and START <= clock < END
    state = "ALLOW" if allowed else "BLOCK"
    print(
        f"[IDX WATCHER HOURS] {state} | {now.isoformat(timespec='seconds')} | "
        "window=Mon-Fri 07:00-18:00 Asia/Jakarta",
        flush=True,
    )
    return 0 if allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
