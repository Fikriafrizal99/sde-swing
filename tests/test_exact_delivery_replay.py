from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.job_runner import delivery as delivery_module
from modules.job_runner import existing_delivery
from modules.job_runner.delivery import _photo_parts, deliver
from modules.job_runner.existing_delivery import (
    ExactDeliveryError,
    ExistingDelivery,
    copy_existing_delivery,
    find_existing_delivery,
    load_preview_selection,
    save_preview_selection,
)
from modules.job_runner.reports import ReportPayload, mark_preview_manifest_delivery, write_payloads
from modules.job_runner.runtime import RunnerContext


def _ctx(tmp_path: Path, job: str) -> SimpleNamespace:
    preview_root = tmp_path / "previews"
    state_root = tmp_path / "state"
    delivery_log = tmp_path / "delivery.jsonl"
    return SimpleNamespace(
        job=job,
        run_id=f"PREVIEW-{job}",
        trade_date=date(2026, 8, 7),
        previews_root=preview_root,
        state_root=state_root,
        scheduler_config={"delivery": {"delivery_log": str(delivery_log)}},
    )


def _append_events(ctx: SimpleNamespace, events: list[dict]) -> None:
    path = Path(ctx.scheduler_config["delivery"]["delivery_log"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")


def _archive_legacy_preview(
    ctx: SimpleNamespace,
    run_id: str,
    records: list[tuple[str, str, str]],
) -> None:
    folder = ctx.previews_root / ctx.trade_date.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    report_types: list[str] = []
    for report_type, filename, body in records:
        canonical = folder / filename
        run_scoped = folder / f"{run_id}_{filename}"
        canonical.write_text("mutable canonical", encoding="utf-8")
        run_scoped.write_text(body, encoding="utf-8")
        files.append(str(canonical))
        report_types.append(report_type)
    (folder / f"{run_id}_preview_manifest.json").write_text(
        json.dumps({
            "run_id": run_id,
            "job": ctx.job,
            "trade_date": ctx.trade_date.isoformat(),
            "files": files,
            "report_types": report_types,
        }),
        encoding="utf-8",
    )


def _sent_event(
    *,
    run_id: str,
    job: str,
    report_type: str,
    sequence: int,
    message_ids: list[int],
    time: str,
    force: bool = False,
    status: str = "SENT",
) -> dict:
    return {
        "time": time,
        "run_id": run_id,
        "job": job,
        "trade_date": "2026-08-07",
        "report_type": report_type,
        "status": status,
        "force_resend": force,
        "delivery_sequence": sequence,
        "delivery_total": 3,
        "message_thread_id": "9",
        "telegram_message_ids": message_ids,
    }


def test_final_watchlist_selects_original_sent_run_not_later_forced_resend(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "final_watchlist")
    original = "SWING-ORIGINAL"
    forced = "SDE-FORCED-RESEND"
    records = [
        ("final_watchlist_summary", "final_watchlist_summary.txt", "ORIGINAL SUMMARY"),
        ("final_watchlist_detail", "final_watchlist_detail_BBCA.txt", "ORIGINAL BBCA CARD"),
        ("final_watchlist_csv", "final_watchlist_csv.txt", "ORIGINAL CSV CAPTION"),
    ]
    _archive_legacy_preview(ctx, original, records)
    _append_events(ctx, [
        _sent_event(run_id=original, job="final_watchlist", report_type=kind, sequence=index,
                    message_ids=[100 + index], time=f"2026-08-07T18:00:0{index}+07:00")
        for index, (kind, _, _) in enumerate(records, start=1)
    ])
    _append_events(ctx, [
        _sent_event(run_id=forced, job="final_watchlist", report_type=kind, sequence=index,
                    message_ids=[200 + index], time=f"2026-08-08T10:00:0{index}+07:00", force=True)
        for index, (kind, _, _) in enumerate(records, start=1)
    ])

    selected = find_existing_delivery(ctx, "final_watchlist")

    assert selected.source_run_id == original
    assert selected.message_count == 3
    assert [path.read_text(encoding="utf-8") for path in selected.preview_paths] == [
        "ORIGINAL SUMMARY",
        "ORIGINAL BBCA CARD",
        "ORIGINAL CSV CAPTION",
    ]


def test_resend_receipt_stays_pinned_when_a_newer_run_appears(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "market_outlook")
    _archive_legacy_preview(ctx, "RUN-A", [
        ("market_outlook", "market_outlook.txt", "APPROVED A"),
    ])
    _append_events(ctx, [
        _sent_event(run_id="RUN-A", job="market_outlook", report_type="market_outlook",
                    sequence=1, message_ids=[301], time="2026-08-07T07:31:00+07:00"),
    ])
    approved = find_existing_delivery(ctx, "market_outlook")
    save_preview_selection(ctx, approved)

    _archive_legacy_preview(ctx, "RUN-B", [
        ("market_outlook", "market_outlook.txt", "UNREVIEWED B"),
    ])
    _append_events(ctx, [
        _sent_event(run_id="RUN-B", job="market_outlook", report_type="market_outlook",
                    sequence=1, message_ids=[302], time="2026-08-07T08:00:00+07:00"),
    ])

    selected = load_preview_selection(ctx, "market_outlook")

    assert selected.source_run_id == "RUN-A"
    assert selected.preview_paths[0].read_text(encoding="utf-8") == "APPROVED A"

    selected.preview_paths[0].write_text("TAMPERED AFTER APPROVAL", encoding="utf-8")
    with pytest.raises(ExactDeliveryError, match="EXACT_PREVIEW_SELECTION_FILES_CHANGED"):
        load_preview_selection(ctx, "market_outlook")


def test_exact_copy_uses_original_message_ids_threads_and_order(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict] = []

    class Response:
        ok = True
        status_code = 200

        def __init__(self, message_id: int):
            self.message_id = message_id

        def json(self):
            return {"ok": True, "result": {"message_id": self.message_id}}

    class Requests:
        @staticmethod
        def post(url, data, timeout):
            calls.append({"url": url, "data": dict(data), "timeout": timeout})
            return Response(900 + len(calls))

    monkeypatch.setattr(existing_delivery, "requests", Requests)
    monkeypatch.setattr(existing_delivery, "_credentials", lambda _ctx: ("TOKEN", "CHAT"))
    source = ExistingDelivery(
        requested_job="post_market",
        trade_date="2026-08-07",
        source_run_id="POST-SOURCE",
        source_job="post_market",
        source_time="2026-08-07T16:30:00+07:00",
        entries=(
            {"report_type": "post_market_heatmap", "delivery_sequence": 1,
             "message_thread_id": "9", "telegram_message_ids": [41]},
            {"report_type": "post_market", "delivery_sequence": 2,
             "message_thread_id": "9", "telegram_message_ids": [42]},
        ),
        preview_paths=(Path("post_market.txt"),),
        preview_manifest=None,
        signature="signature",
    )
    delivery_log = tmp_path / "delivery.jsonl"
    ctx = SimpleNamespace(
        run_id="REPLAY",
        job="post_market",
        scheduler_config={"delivery": {"delivery_log": str(delivery_log)}},
    )

    results = copy_existing_delivery(ctx, source)

    assert [call["data"]["message_id"] for call in calls] == [41, 42]
    assert all(call["url"].endswith("/copyMessage") for call in calls)
    assert all(call["data"]["from_chat_id"] == "CHAT" for call in calls)
    assert all(call["data"]["message_thread_id"] == "9" for call in calls)
    assert [item["telegram_message_ids"] for item in results] == [[901], [902]]
    assert all(item["status"] == "SENT" for item in results)
    audit = [json.loads(line) for line in delivery_log.read_text(encoding="utf-8").splitlines()]
    assert [item["source_telegram_message_ids"] for item in audit] == [[41], [42]]
    assert all(item["force_resend"] is True for item in audit)


def test_explicit_photo_caption_is_one_complete_card(tmp_path: Path) -> None:
    image = tmp_path / "BBCA.png"
    image.write_bytes(b"png")
    payload = ReportPayload("final_watchlist_detail", "BBCA.txt", "X" * 2_000, symbol="BBCA")
    setattr(payload, "attachment_path", image)
    setattr(payload, "caption", "APPROVED COMPACT CARD")

    caption, followups = _photo_parts(payload, 4_000)

    assert caption == "APPROVED COMPACT CARD"
    assert followups == []


def _delivery_ctx(tmp_path: Path, run_id: str) -> RunnerContext:
    return RunnerContext(
        job="final_watchlist",
        config_path=tmp_path / "pipeline.json",
        scheduler_config_path=tmp_path / "scheduler.json",
        trade_date=date(2026, 8, 7),
        run_id=run_id,
        config={"paths": {"telegram_config": str(tmp_path / "telegram.json")}},
        scheduler_config={
            "paths": {
                "preview_root": str(tmp_path / "previews"),
                "state_root": str(tmp_path / "state"),
            },
            "delivery": {
                "idempotency_index": str(tmp_path / "state" / "idempotency.json"),
                "delivery_log": str(tmp_path / "state" / "delivery.jsonl"),
                "failed_root": str(tmp_path / "failed"),
                "topic_routing": {},
            },
            "telegram": {"maximum_message_length": 4_000},
        },
        calendar_config={},
    )


def test_live_delivery_with_explicit_caption_sends_only_one_photo(monkeypatch, tmp_path: Path) -> None:
    image = tmp_path / "BBCA.png"
    image.write_bytes(b"png")
    payload = ReportPayload("final_watchlist_detail", "BBCA.txt", "FULL " + "X" * 2_000, symbol="BBCA")
    setattr(payload, "attachment_path", image)
    setattr(payload, "caption", "APPROVED COMPACT CARD")
    photos: list[str] = []
    texts: list[str] = []

    def send_photo(_ctx, _payload, caption):
        photos.append(caption)
        return {"ok": True, "result": {"message_id": 701}}

    def send_text(_ctx, _payload, text=None, **_kwargs):
        texts.append(str(text or ""))
        return {"ok": True, "result": {"message_id": 702}}

    monkeypatch.setattr(delivery_module, "_send_photo", send_photo)
    monkeypatch.setattr(delivery_module, "_send_telegram", send_text)

    result = deliver(_delivery_ctx(tmp_path, "RUN-ONE-CARD"), [payload])

    assert photos == ["APPROVED COMPACT CARD"]
    assert texts == []
    assert result[0]["status"] == "SENT"
    assert result[0]["part_count"] == 1
    assert result[0]["telegram_message_ids"] == [701]


def test_photo_failure_falls_back_to_compact_caption_not_verbose_card(monkeypatch, tmp_path: Path) -> None:
    image = tmp_path / "BBCA.png"
    image.write_bytes(b"png")
    payload = ReportPayload("final_watchlist_detail", "BBCA.txt", "FULL " + "X" * 2_000, symbol="BBCA")
    setattr(payload, "attachment_path", image)
    setattr(payload, "caption", "APPROVED COMPACT CARD")
    texts: list[str] = []

    monkeypatch.setattr(
        delivery_module,
        "_send_photo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("photo unavailable")),
    )

    def send_text(_ctx, _payload, text=None, **_kwargs):
        texts.append(str(text or ""))
        return {"ok": True, "result": {"message_id": 703}}

    monkeypatch.setattr(delivery_module, "_send_telegram", send_text)

    result = deliver(_delivery_ctx(tmp_path, "RUN-COMPACT-FALLBACK"), [payload])

    assert texts == ["APPROVED COMPACT CARD"]
    assert result[0]["status"] == "SENT_WITH_TEXT_FALLBACK"
    assert result[0]["telegram_message_ids"] == [703]


def test_preview_bundle_archives_exact_outbound_caption_and_attachment(tmp_path: Path) -> None:
    image = tmp_path / "BBCA.png"
    image.write_bytes(b"original-image")
    ctx = RunnerContext(
        job="final_watchlist",
        config_path=tmp_path / "pipeline.json",
        scheduler_config_path=tmp_path / "scheduler.json",
        trade_date=date(2026, 8, 7),
        run_id="RUN-BUNDLE",
        config={"paths": {"reports_root": str(tmp_path / "reports")}},
        scheduler_config={
            "paths": {"preview_root": str(tmp_path / "previews")},
            "telegram": {"maximum_message_length": 4_000},
        },
        calendar_config={},
    )
    payload = ReportPayload(
        "final_watchlist_detail",
        "final_watchlist_detail_BBCA.txt",
        "FULL AUDIT TEXT " + "X" * 2_000,
        symbol="BBCA",
    )
    setattr(payload, "attachment_path", image)
    setattr(payload, "caption", "APPROVED COMPACT CARD")
    payloads = [payload]

    preview_paths = write_payloads(ctx, payloads)
    manifest_path = ctx.previews_root / "2026-08-07" / "RUN-BUNDLE_preview_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["payloads"][0]

    assert "APPROVED COMPACT CARD" in preview_paths[0].read_text(encoding="utf-8")
    assert "FULL AUDIT TEXT" not in preview_paths[0].read_text(encoding="utf-8")
    assert record["telegram_parts"] == [{"kind": "photo", "text": "APPROVED COMPACT CARD"}]
    archived = Path(record["attachment_archive"])
    assert archived.read_bytes() == b"original-image"
    image.write_bytes(b"mutated-latest-image")
    assert archived.read_bytes() == b"original-image"

    mark_preview_manifest_delivery(ctx, [{
        "delivery_sequence": 1,
        "report_type": "final_watchlist_detail",
        "status": "SENT",
        "part_count": 1,
        "telegram_message_ids": [501],
        "message_thread_id": "9",
    }])
    finalized = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert finalized["state"] == "DELIVERED"
    assert finalized["delivery_complete"] is True


def test_post_market_exact_preview_includes_archived_heatmap_and_rejects_tamper(
    tmp_path: Path,
) -> None:
    heatmap = tmp_path / "market_heatmap.png"
    heatmap.write_bytes(b"exact-heatmap")
    ctx = RunnerContext(
        job="post_market",
        config_path=tmp_path / "pipeline.json",
        scheduler_config_path=tmp_path / "scheduler.json",
        trade_date=date(2026, 8, 7),
        run_id="RUN-POST-BUNDLE",
        config={"paths": {"reports_root": str(tmp_path / "reports")}},
        scheduler_config={
            "paths": {
                "preview_root": str(tmp_path / "previews"),
                "state_root": str(tmp_path / "state"),
            },
            "delivery": {"delivery_log": str(tmp_path / "delivery.jsonl")},
            "telegram": {"maximum_message_length": 4_000},
        },
        calendar_config={},
    )
    image_payload = ReportPayload(
        "post_market_heatmap",
        "post_market_heatmap.txt",
        "",
        topic="post_market",
    )
    setattr(image_payload, "attachment_path", heatmap)
    setattr(image_payload, "caption", "")
    text_payload = ReportPayload(
        "post_market",
        "post_market.txt",
        "APPROVED POST MARKET CARD",
        topic="post_market",
    )

    write_payloads(ctx, [image_payload, text_payload])
    sent = [
        _sent_event(
            run_id=ctx.run_id,
            job="post_market",
            report_type="post_market_heatmap",
            sequence=1,
            message_ids=[801],
            time="2026-08-07T16:30:01+07:00",
        ),
        _sent_event(
            run_id=ctx.run_id,
            job="post_market",
            report_type="post_market",
            sequence=2,
            message_ids=[802],
            time="2026-08-07T16:30:02+07:00",
        ),
    ]
    _append_events(ctx, sent)
    mark_preview_manifest_delivery(ctx, sent)

    selected = find_existing_delivery(ctx, "post_market")

    assert selected.message_count == 2
    assert [path.suffix for path in selected.preview_paths] == [".txt", ".png", ".txt"]
    archived_heatmap = selected.preview_paths[1]
    assert archived_heatmap.read_bytes() == b"exact-heatmap"

    archived_heatmap.write_bytes(b"tampered-heatmap")
    with pytest.raises(ExactDeliveryError, match="EXACT_PREVIEW_FILE_INTEGRITY_FAILED"):
        find_existing_delivery(ctx, "post_market")
