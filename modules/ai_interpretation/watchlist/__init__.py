"""Isolated Final Watchlist AI interpretation subsystem.

This package is downstream-only and must not share runtime state, caches, queues,
prompts, or delivery semantics with News/IDX Disclosure AI paths.
"""

from . import service as _service
from .validator import validate_numbers, validate_response

# Keep provider orchestration in service.py and policy validation in the
# Watchlist-AI-only validator. These rebindings are local to this package and
# cannot affect News/IDX Disclosure AI readers.
_service._validate_numbers = validate_numbers
_service.WatchlistAIService._validate = staticmethod(validate_response)

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
    "validate_response",
]
