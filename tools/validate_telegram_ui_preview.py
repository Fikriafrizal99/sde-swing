#!/usr/bin/env python3
"""Validate current Telegram UI previews for length, HTML, and contract drift."""
from __future__ import annotations

import argparse
import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "span", "tg-spoiler", "a", "code", "pre", "blockquote"}
FORBIDDEN_PATTERNS = [
    re.compile(r"\{[a-zA-Z_][^}]*\}"),
    re.compile(r"\bNone\b", re.I),
    re.compile(r"\bNaN\b", re.I),
    re.compile(r"\\n|\\t"),
]
SUPERSEDED_REPORT_TYPES = {"daily_signal_recap", "closing_bell"}


class TelegramHtmlValidator(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag not in ALLOWED_TAGS:
            self.errors.append(f"unsupported tag <{tag}>")
        if tag not in {"br"}:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            self.errors.append(f"unexpected closing </{tag}>")
            return
        expected = self.stack.pop()
        if expected != tag:
            self.errors.append(f"mismatched </{tag}>; expected </{expected}>")

    def close(self) -> None:
        super().close()
        if self.stack:
            self.errors.append("unclosed tags: " + ", ".join(self.stack))


def read_manifest(folder: Path) -> dict:
    path = folder / "PREVIEW_MANIFEST.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview-dir", default="data/output/telegram_ui_preview/scheduled")
    parser.add_argument("--max-length", type=int, default=4000)
    args = parser.parse_args()

    folder = (ROOT / args.preview_dir).resolve()
    files = sorted(folder.glob("*.txt"))
    manifest = read_manifest(folder)
    results = []
    errors: list[str] = []

    for path in files:
        text = path.read_text(encoding="utf-8")
        validator = TelegramHtmlValidator()
        validator.feed(text)
        validator.close()
        file_errors = list(validator.errors)
        if len(text) > args.max_length:
            file_errors.append(f"message too long: {len(text)} > {args.max_length}")
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.search(text):
                file_errors.append(f"forbidden pattern: {pattern.pattern}")
        if "SDE SWING" not in text:
            file_errors.append("missing SDE SWING header")
        if "━━━━━━━━━━━━━━━━━━━━━━━━━━" not in text:
            file_errors.append("missing standard separator")
        results.append({
            "file": path.name,
            "chars": len(text),
            "status": "PASS" if not file_errors else "FAIL",
            "errors": file_errors,
        })
        errors.extend(f"{path.name}: {item}" for item in file_errors)

    expected_count = manifest.get("report_count")
    if not isinstance(expected_count, int):
        errors.append("PREVIEW_MANIFEST.json missing integer report_count")
    elif len(files) != expected_count:
        errors.append(f"preview file count mismatch: {len(files)} != manifest {expected_count}")

    contract = str(manifest.get("presentation_contract") or "")
    if contract != "CURRENT_ENHANCED_RUNTIME_ONLY":
        errors.append(f"unexpected presentation contract: {contract or 'MISSING'}")

    manifest_types = {
        str(item.get("report_type") or "")
        for item in manifest.get("reports", [])
        if isinstance(item, dict)
    }
    stale = sorted(SUPERSEDED_REPORT_TYPES & manifest_types)
    if stale:
        errors.append("superseded report types present: " + ", ".join(stale))

    report = {
        "preview_dir": str(folder),
        "file_count": len(files),
        "expected_count": expected_count,
        "max_length": args.max_length,
        "presentation_contract": contract,
        "status": "PASS" if not errors else "FAIL",
        "results": results,
        "errors": errors,
    }
    output = folder.parent / "TELEGRAM_UI_VALIDATION.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
