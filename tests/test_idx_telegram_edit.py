from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from modules.idx_disclosure.models import IDXDisclosure
from modules.idx_disclosure.telegram_delivery import TelegramNewsDelivery


JAKARTA = ZoneInfo("Asia/Jakarta")


class Response:
    ok = True
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class Session:
    def __init__(self):
        self.calls = []

    def post(self, url, *, data, timeout):
        self.calls.append((url, data, timeout))
        if url.endswith("/sendMessage"):
            return Response({"ok": True, "result": {"message_id": 321}})
        if url.endswith("/editMessageText"):
            return Response({"ok": True, "result": {"message_id": 321}})
        raise AssertionError(url)


def _disclosure():
    return IDXDisclosure(
        id2="telegram-edit-test",
        ticker="TEST",
        announcement_no="001/TEST/2026",
        published_at=datetime(2026, 8, 20, 13, 40, tzinfo=JAKARTA),
        title="Keterbukaan Informasi",
        subject="",
        idx_created_at=None,
        attachments=(),
    )


def test_send_returns_message_id_then_edit_uses_same_message(tmp_path: Path):
    scheduler = tmp_path / "scheduler.json"
    telegram = tmp_path / "telegram.json"
    scheduler.write_text(
        json.dumps({"delivery": {"topic_routing": {"news": "1451"}}}),
        encoding="utf-8",
    )
    telegram.write_text(
        json.dumps({"telegram": {"bot_token": "TOKEN", "chat_id": "-100123"}}),
        encoding="utf-8",
    )
    session = Session()
    delivery = TelegramNewsDelivery(
        scheduler_config_path=scheduler,
        telegram_config_path=telegram,
        session=session,
        environ={},
    )

    message_id = delivery.send(_disclosure(), "official")
    delivery.edit(_disclosure(), "official + ai", message_id=message_id)

    assert message_id == 321
    send_url, send_data, _ = session.calls[0]
    edit_url, edit_data, _ = session.calls[1]
    assert send_url.endswith("/sendMessage")
    assert send_data["message_thread_id"] == "1451"
    assert edit_url.endswith("/editMessageText")
    assert edit_data["message_id"] == 321
    assert "message_thread_id" not in edit_data
