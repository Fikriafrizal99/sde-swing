from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


CONFIG_PATH = Path("config/idx_disclosure.json")


def _db_path() -> Path:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return Path(cfg["state"]["sqlite_path"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Show latest isolated IDX AI reader state")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    db = _db_path()
    if not db.exists():
        print(f"IDX AI state database not found: {db}")
        return 1

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                a.disclosure_id,
                d.ticker,
                d.title,
                a.status,
                a.attempts,
                a.model,
                a.input_chars,
                a.last_error,
                a.queued_at,
                a.next_attempt_at,
                a.processed_at,
                a.telegram_sent_at
            FROM idx_disclosure_ai a
            JOIN idx_disclosures d ON d.id2=a.disclosure_id
            ORDER BY COALESCE(a.processed_at, a.queued_at) DESC
            LIMIT ?
            """,
            (max(1, int(args.limit)),),
        ).fetchall()

    if not rows:
        print("No IDX AI queue records yet.")
        return 0

    for row in rows:
        payload = dict(row)
        payload["title"] = str(payload.get("title") or "")[:120]
        print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
