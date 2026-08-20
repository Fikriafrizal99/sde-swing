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
    parser = argparse.ArgumentParser(
        description="Reset one isolated IDX AI queue record without resending the official IDX message"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--disclosure-id")
    group.add_argument("--ticker")
    args = parser.parse_args()

    db = _db_path()
    if not db.exists():
        print(json.dumps({"ok": False, "error": f"DB_NOT_FOUND:{db}"}))
        return 1

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        if args.disclosure_id:
            row = conn.execute(
                """
                SELECT a.disclosure_id, d.ticker, d.title, a.status, a.attempts
                FROM idx_disclosure_ai a
                JOIN idx_disclosures d ON d.id2=a.disclosure_id
                WHERE a.disclosure_id=?
                """,
                (args.disclosure_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT a.disclosure_id, d.ticker, d.title, a.status, a.attempts
                FROM idx_disclosure_ai a
                JOIN idx_disclosures d ON d.id2=a.disclosure_id
                WHERE UPPER(d.ticker)=UPPER(?)
                ORDER BY d.published_at DESC, a.queued_at DESC
                LIMIT 1
                """,
                (args.ticker,),
            ).fetchone()

        if row is None:
            print(json.dumps({"ok": False, "error": "AI_QUEUE_RECORD_NOT_FOUND"}))
            return 1

        conn.execute(
            """
            UPDATE idx_disclosure_ai
            SET status='PENDING', attempts=0, next_attempt_at=NULL,
                summary_json=NULL, model=NULL, source_hash=NULL, input_chars=0,
                last_error=NULL, processed_at=NULL, telegram_sent_at=NULL
            WHERE disclosure_id=?
            """,
            (str(row["disclosure_id"]),),
        )

    print(
        json.dumps(
            {
                "ok": True,
                "disclosure_id": str(row["disclosure_id"]),
                "ticker": str(row["ticker"]),
                "title": str(row["title"])[:120],
                "previous_status": str(row["status"]),
                "previous_attempts": int(row["attempts"] or 0),
                "new_status": "PENDING",
                "new_attempts": 0,
                "official_idx_delivery_unchanged": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
