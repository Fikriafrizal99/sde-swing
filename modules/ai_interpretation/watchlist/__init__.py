"""Isolated Final Watchlist AI interpretation subsystem.

This package is downstream-only and must not share runtime state, caches, queues,
prompts, or delivery semantics with News/IDX Disclosure AI paths.
"""

from . import service as _service
from .validator import validate_numbers

# Keep the provider service focused on orchestration while the hardened numeric
# policy lives in its own Watchlist-AI-only module. This rebinding is local to
# the isolated package and cannot affect News/IDX Disclosure AI readers.
_service._validate_numbers = validate_numbers

from .service import (  # noqa: E402
    ProviderAttempt,
    WatchlistAIResult,
    WatchlistAIService,
    build_watchlist_context,
)

__all__ = [
    "ProviderAttempt",
    "WatchlistAIResult",
    "WatchlistAIService",
    "build_watchlist_context",
    "validate_numbers",
]
