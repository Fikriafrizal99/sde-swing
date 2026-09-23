from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from tools.final_watchlist_snapshot import _clear_canonical_detail_previews


def test_clear_canonical_detail_previews_keeps_run_scoped_and_other_files(tmp_path) -> None:
    trade_date = date(2026, 9, 23)
    folder = tmp_path / trade_date.isoformat()
    folder.mkdir(parents=True)

    stale_gula = folder / "final_watchlist_detail_GULA.txt"
    stale_pgeo = folder / "final_watchlist_detail_PGEO.txt"
    run_scoped = folder / "SDE-FINAL-WATCHLIST-20260923-190240-bcd1_final_watchlist_detail_GULA.txt"
    summary = folder / "final_watchlist_summary.txt"
    csv_preview = folder / "final_watchlist_csv.txt"

    for path in (stale_gula, stale_pgeo, run_scoped, summary, csv_preview):
        path.write_text("fixture", encoding="utf-8")

    ctx = SimpleNamespace(previews_root=tmp_path, trade_date=trade_date)

    removed = _clear_canonical_detail_previews(ctx)

    assert set(removed) == {stale_gula, stale_pgeo}
    assert not stale_gula.exists()
    assert not stale_pgeo.exists()
    assert run_scoped.exists()
    assert summary.exists()
    assert csv_preview.exists()
