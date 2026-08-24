"""Read-only market news utilities for SDE Swing.

News filtering, ranking, freshness, dedupe state, and delivery routing remain
unchanged. FTJ branding is applied only when the already-built digest is split
for Telegram delivery, so presentation cannot influence news selection or the
existing dedupe signature.
"""

from __future__ import annotations

from functools import wraps

from modules.branding import apply_ftj_branding


def _install_ftj_news_delivery_branding() -> None:
    from . import news_monitor as _base

    if getattr(_base, "_ftj_delivery_branding_installed", False):
        return

    original_split = _base._split_text

    @wraps(original_split)
    def branded_split(text: str, limit: int = 4000):
        return original_split(apply_ftj_branding(text), limit)

    _base._split_text = branded_split
    _base._ftj_delivery_branding_installed = True


_install_ftj_news_delivery_branding()
