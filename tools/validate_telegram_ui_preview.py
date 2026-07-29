#!/usr/bin/env python3
"""Validate generated Telegram UI previews for length, HTML, and placeholders."""
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview-dir", default="data/output/telegram_ui_preview/scheduled")
    parser.add_argument("--max-length", type=int, default=4000)
    args = parser.parse_args()

    folder = (ROOT / args.preview_dir).resolve()
    files = sorted(folder.glob("*.txt"))
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
        results.append({"file": path.name, "chars": len(text), "status": "PASS" if not file_errors else "FAIL", "errors": file_errors})
        errors.extend(f"{path.name}: {item}" for item in file_errors)

    report = {
        "preview_dir": str(folder),
        "file_count": len(files),
        "max_length": args.max_length,
        "status": "PASS" if not errors and len(files) == 7 else "FAIL",
        "results": results,
        "errors": errors,
    }
    output = folder.parent / "TELEGRAM_UI_VALIDATION.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
