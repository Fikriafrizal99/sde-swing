"""SDE Swing scheduled job runner package.

Human-facing integrated report payloads are branded here at the package
boundary. Engine/report builders keep their existing internal contracts and
filenames; only the final payload text is renamed for FTJ Community.
"""

from __future__ import annotations

from functools import wraps

from modules.branding import apply_ftj_branding


def _install_ftj_report_payload_branding() -> None:
    """Brand every integrated ReportPayload exactly once.

    Keeping this at the payload boundary makes auto/manual/resend paths share
    one title policy without changing engine facts or individual formatters.
    """
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


_install_ftj_report_payload_branding()
