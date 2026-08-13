from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "hotfix-exit-volume.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_hotfix_workflow_uses_read_only_permissions() -> None:
    text = _workflow_text()

    assert re.search(r"(?m)^permissions:\s*\n\s{2}contents:\s*read\s*$", text)
    assert not re.search(r"(?im)^\s*contents:\s*write\s*$", text)
    assert "write-all" not in text.lower()
    assert "pull_request_target" not in text


def test_hotfix_checkout_does_not_persist_credentials_or_force_a_ref() -> None:
    text = _workflow_text()

    assert re.search(r"(?m)^\s{10}persist-credentials:\s*false\s*$", text)
    assert not re.search(r"(?m)^\s+ref:\s*", text)
    assert "GITHUB_TOKEN" not in text
    assert "GH_TOKEN" not in text
    assert re.search(r"(?i)\bPAT\b", text) is None
    assert "secrets." not in text


def test_hotfix_workflow_has_no_direct_or_inline_mutation_path() -> None:
    text = _workflow_text()
    forbidden_git = re.compile(
        r"(?i)\bgit\s+(?:add|commit|push|checkout|switch|reset|clean|merge|"
        r"rebase|amend|update-ref)\b"
    )
    forbidden_inline_writes = (
        ".write_text(",
        ".write_bytes(",
        ".unlink(",
        ".rename(",
        ".replace(",
    )

    assert forbidden_git.search(text) is None
    assert all(fragment not in text for fragment in forbidden_inline_writes)
    assert "tests/test_signal_quality_v160.py" in text
    assert "git diff --exit-code" in text
    assert "git diff --cached --exit-code" in text
