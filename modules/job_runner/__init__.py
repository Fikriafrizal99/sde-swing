"""SDE Swing scheduled job runner package.

Human-facing integrated report payloads are branded here at the package
boundary. Engine/report builders keep their existing internal contracts and
filenames; only the final payload presentation is adjusted here.
"""

from __future__ import annotations

from dataclasses import replace
from functools import wraps

from modules.branding import apply_ftj_branding


FINAL_WATCHLIST_MAX_DETAIL_CARDS = 10


def _install_ftj_report_payload_branding() -> None:
    """Brand every integrated ReportPayload exactly once."""
    from . import reports as _reports

    payload_class = _reports.ReportPayload
    if getattr(payload_class, "_ftj_branding_installed", False):
        return

    original_init = payload_class.__init__

    @wraps(original_init)
    def branded_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.text = apply_ftj_branding(self.text)

    payload_class.__init__ = branded_init
    payload_class._ftj_branding_installed = True


def _install_final_watchlist_presentation_contract() -> None:
    """Lock the operator-approved Final Watchlist Telegram bundle.

    Presentation-only rules:
    - detail cards are actionable BUY READY / BUY CANDIDATE variants only;
    - Telegram emits at most 10 detail cards and every emitted detail has a chart;
    - chart failures remain visible in logs/CSV but never become text-only cards;
    - WATCH/WAIT/AVOID stay in summary/CSV and never consume chart-card slots.

    No score, decision, trade-plan, broker, or engine calculation is changed.
    """
    from . import enhanced_daily_reports as _daily

    _daily.FINAL_WATCHLIST_DETAIL_DECISIONS = {
        "BUY",
        "BUY READY",
        "BUY CONFIRMED",
        "BUY CANDIDATE",
        "BUY ON TRIGGER",
    }

    builder_class = _daily.EnhancedDailyReportBuilder
    if getattr(builder_class, "_final_watchlist_contract_installed", False):
        return

    original_build = builder_class.build_final_watchlist

    @wraps(original_build)
    def locked_build(self, data):
        configured_limit = int(getattr(self, "max_watchlist_messages", 0) or 0)
        # Build all actionable candidates so a chart failure in a higher-ranked
        # row can be replaced by the next chart-backed candidate. Only the
        # presentation result is capped; engine facts and CSV remain complete.
        self.max_watchlist_messages = 0
        try:
            artifacts = list(original_build(self, data))
        finally:
            self.max_watchlist_messages = configured_limit

        result = []
        chart_detail_count = 0
        for artifact in artifacts:
            report_type = str(getattr(artifact, "report_type", "") or "").lower()
            if report_type == "final_watchlist_summary":
                updated_text = str(artifact.text or "").replace(
                    "📌 5 kartu berikut adalah 5 saham terbaik berdasarkan status eksekusi dan Final Score.",
                    "📌 Maksimal 10 chart-card berikut memuat BUY READY / BUY CANDIDATE terbaik berdasarkan status eksekusi dan Final Score.",
                )
                result.append(replace(artifact, text=updated_text) if updated_text != artifact.text else artifact)
                continue
            if report_type == "final_watchlist_detail":
                attachment = getattr(artifact, "attachment_path", None)
                if attachment in (None, ""):
                    continue
                if chart_detail_count >= FINAL_WATCHLIST_MAX_DETAIL_CARDS:
                    continue
                chart_detail_count += 1
            result.append(artifact)
        return result

    builder_class.build_final_watchlist = locked_build
    builder_class._final_watchlist_contract_installed = True


_install_ftj_report_payload_branding()
_install_final_watchlist_presentation_contract()
