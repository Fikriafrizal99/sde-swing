from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from modules.idx_disclosure.ai_reader import GroqDisclosureAIReader
from modules.idx_disclosure.models import IDXDisclosure


JAKARTA = ZoneInfo("Asia/Jakarta")


class _Response:
    status_code = 200

    def json(self):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "summary": "Perseroan menyampaikan fakta dalam dokumen resmi.",
                                "key_points": [],
                                "important_dates": [],
                                "important_values": [],
                                "related_parties": [],
                                "document_type": "Keterbukaan informasi",
                            }
                        )
                    }
                }
            ]
        }


class _Session:
    def __init__(self):
        self.post_kwargs = None

    def post(self, url, **kwargs):
        self.post_kwargs = kwargs
        return _Response()


def _disclosure() -> IDXDisclosure:
    return IDXDisclosure(
        id2="test-json-mode",
        ticker="TEST",
        announcement_no="TEST/001",
        published_at=datetime(2026, 8, 20, 13, 0, tzinfo=JAKARTA),
        title="Keterbukaan Informasi",
        subject="",
        idx_created_at=None,
    )


def test_gpt_oss_request_forces_json_and_hides_reasoning():
    session = _Session()
    reader = GroqDisclosureAIReader(
        api_key="test-key",
        model="openai/gpt-oss-120b",
        session=session,
    )

    payload = reader._groq(_disclosure(), "Isi dokumen resmi yang cukup panjang untuk diringkas.")

    assert payload["summary"].startswith("Perseroan")
    request = session.post_kwargs["json"]
    assert request["response_format"] == {"type": "json_object"}
    assert request["reasoning_format"] == "hidden"
    assert request["reasoning_effort"] == "low"
