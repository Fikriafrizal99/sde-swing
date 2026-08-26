from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

from swing_utils import file_sha256
from modules.job_runner import existing_delivery as exact
from modules.job_runner import final_watchlist_snapshot as snapshot
from modules.job_runner.runtime import write_json


def _preview(path: Path, run_id: str, report_type: str, body: str) -> Path:
    path.write_text(
        f"Run ID: {run_id}\nTrade Date: 2026-08-26\nReport Type: {report_type}\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _context(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    return SimpleNamespace(
        state_root=state,
        trade_date=date(2026, 8, 26),
        run_id="RESEND-RUN",
        scheduler_config={
            "delivery": {"delivery_log": str(tmp_path / "delivery.jsonl")},
            "telegram": {"maximum_message_length": 4000},
        },
    )


def _install_selection(tmp_path: Path, ctx) -> snapshot.ExistingDelivery:
    run_id = "PREVIEW-RUN"
    folder = tmp_path / "preview"
    folder.mkdir()

    summary = _preview(folder / "summary.txt", run_id, "final_watchlist_summary", "SUMMARY")
    detail = _preview(folder / "detail.txt", run_id, "final_watchlist_detail", "DETAIL")
    csv_preview = _preview(folder / "csv.txt", run_id, "final_watchlist_csv", "CSV")
    chart = folder / "S1.png"
    chart.write_bytes(b"chart")
    csv_file = folder / "watchlist.csv"
    csv_file.write_text("symbol,decision\nS1,BUY READY\n", encoding="utf-8")

    manifest = {
        "schema": "SDE_DELIVERY_PREVIEW_BUNDLE_V1",
        "run_id": run_id,
        "job": "final_watchlist",
        "trade_date": "2026-08-26",
        "state": "PREPARED",
        "payload_count": 3,
        "report_types": [
            "final_watchlist_summary",
            "final_watchlist_detail",
            "final_watchlist_csv",
        ],
        "payloads": [
            {
                "sequence": 1,
                "report_type": "final_watchlist_summary",
                "signature": "sig-summary",
                "run_scoped_preview": str(summary),
                "preview_sha256": file_sha256(summary),
                "attachment_archive": "",
                "attachment_sha256": "",
                "telegram_parts": [{"kind": "text", "text": "SUMMARY"}],
            },
            {
                "sequence": 2,
                "report_type": "final_watchlist_detail",
                "signature": "sig-detail",
                "run_scoped_preview": str(detail),
                "preview_sha256": file_sha256(detail),
                "attachment_archive": str(chart),
                "attachment_sha256": file_sha256(chart),
                "telegram_parts": [{"kind": "photo", "text": "DETAIL"}],
            },
            {
                "sequence": 3,
                "report_type": "final_watchlist_csv",
                "signature": "sig-csv",
                "run_scoped_preview": str(csv_preview),
                "preview_sha256": file_sha256(csv_preview),
                "attachment_archive": str(csv_file),
                "attachment_sha256": file_sha256(csv_file),
                "telegram_parts": [{"kind": "document", "text": "CSV"}],
            },
        ],
    }
    manifest_path = folder / f"{run_id}_preview_manifest.json"
    write_json(manifest_path, manifest)

    approved = [summary, detail, chart, csv_preview, csv_file]
    routes = {"1": "9", "2": "9", "3": "9"}
    manifest_hash = file_sha256(manifest_path)
    selection = {
        "schema": snapshot.SNAPSHOT_SELECTION_SCHEMA,
        "job": "final_watchlist",
        "trade_date": "2026-08-26",
        "snapshot_run_id": run_id,
        "snapshot_manifest": str(manifest_path),
        "snapshot_manifest_sha256": manifest_hash,
        "snapshot_signature": snapshot._snapshot_signature(manifest_hash, routes),
        "message_thread_ids": routes,
        "approved_paths": [str(path) for path in approved],
        "approved_sha256": [file_sha256(path) for path in approved],
        "report_types": manifest["report_types"],
        "payload_count": 3,
        "detail_count": 1,
        "max_detail_cards": 10,
        "created_at": "2026-08-26T22:00:00+07:00",
    }
    write_json(ctx.state_root / "final_watchlist_presentation_selection.json", selection)
    return snapshot.load_snapshot(ctx)


def test_snapshot_replay_is_checkpointed_and_does_not_duplicate(monkeypatch, tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    source = _install_selection(tmp_path, ctx)
    next_id = {"value": 100}

    def fake_send(_ctx, _entry, _spec, message_ids=None):
        next_id["value"] += 1
        message_ids.append(next_id["value"])
        return message_ids

    monkeypatch.setattr(exact, "_send_archived_entry", fake_send)

    first = snapshot.replay_snapshot(ctx, source)
    second = snapshot.replay_snapshot(ctx, source)

    assert [item["status"] for item in first] == ["SENT", "SENT", "SENT"]
    assert [item["status"] for item in second] == [
        "SNAPSHOT_ALREADY_SENT",
        "SNAPSHOT_ALREADY_SENT",
        "SNAPSHOT_ALREADY_SENT",
    ]
    assert next_id["value"] == 103


def test_read_timeout_is_uncertain_but_later_payload_still_sends(monkeypatch, tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    source = _install_selection(tmp_path, ctx)

    def fake_send(_ctx, entry, _spec, message_ids=None):
        sequence = int(entry["delivery_sequence"])
        if sequence == 2:
            raise RuntimeError("ReadTimeout: Read timed out. (read timeout=60)")
        message_ids.append(200 + sequence)
        return message_ids

    monkeypatch.setattr(exact, "_send_archived_entry", fake_send)

    first = snapshot.replay_snapshot(ctx, source)
    second = snapshot.replay_snapshot(ctx, source)

    assert [item["status"] for item in first] == [
        "SENT",
        "DELIVERY_STATE_UNCERTAIN",
        "SENT",
    ]
    assert [item["status"] for item in second] == [
        "SNAPSHOT_ALREADY_SENT",
        "DELIVERY_STATE_UNCERTAIN",
        "SNAPSHOT_ALREADY_SENT",
    ]
