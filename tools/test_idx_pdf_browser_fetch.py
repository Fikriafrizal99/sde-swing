from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from modules.idx_disclosure.browser_client import PlaywrightAnnouncementClient
from modules.idx_disclosure.normalizer import IDXPayloadError, normalize_reply


JAKARTA = ZoneInfo("Asia/Jakarta")
CONFIG_PATH = Path("config/idx_disclosure.json")


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    request_cfg = cfg.get("request", {})
    browser_cfg = request_cfg.get("browser", {})
    client = PlaywrightAnnouncementClient(
        endpoint=str(cfg["endpoint"]),
        bootstrap_url=str(request_cfg.get("bootstrap_url")),
        timeout_seconds=float(browser_cfg.get("timeout_seconds", 25)),
        emiten_type=str(request_cfg.get("emiten_type", "*")),
        language=str(request_cfg.get("language", "id")),
        keyword=str(request_cfg.get("keyword", "")),
        browser_channels=tuple(browser_cfg.get("channels", ["chrome", "msedge"])),
        headless=bool(browser_cfg.get("headless", False)),
        bootstrap_wait_ms=int(browser_cfg.get("bootstrap_wait_ms", 2500)),
        max_retries=int(browser_cfg.get("max_retries", 1)),
    )

    try:
        page = client.fetch_page(
            trade_date=datetime.now(JAKARTA).date(),
            index_from=0,
            page_size=int(request_cfg.get("page_size", 50)),
        )
        selected = None
        for raw in page.replies:
            try:
                disclosure = normalize_reply(raw)
            except IDXPayloadError:
                continue
            documents = [item for item in disclosure.attachments if not item.is_attachment]
            if not documents:
                documents = list(disclosure.attachments)
            if documents:
                selected = (disclosure, documents[0])
                break

        if selected is None:
            print(json.dumps({"ok": False, "error": "NO_DOCUMENT_FOUND"}))
            return 1

        disclosure, document = selected
        max_bytes = int(cfg.get("ai_reader", {}).get("max_pdf_bytes", 25_000_000))
        data = client.fetch_document_bytes(document.url, max_bytes=max_bytes)
        payload = {
            "ok": True,
            "transport": client.transport_name,
            "ticker": disclosure.ticker,
            "title": disclosure.title,
            "filename": document.filename,
            "bytes": len(data),
            "looks_like_pdf": data[:5] == b"%PDF-",
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}:{exc}"},
                ensure_ascii=False,
            )
        )
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
