"""Isolated Final Watchlist AI interpretation subsystem.

This package is downstream-only and must not share runtime state, caches, queues,
prompts, or delivery semantics with News/IDX Disclosure AI paths.
"""

from .service import (
    ProviderAttempt,
    WatchlistAIResult,
    WatchlistAIService as BaseWatchlistAIService,
    build_watchlist_context,
)
from .presentation_service import PresentationWatchlistAIService
from .normalizer import build_presentation_context
from .validator import validate_numbers, validate_response

# The public WatchlistAIService used by the runtime is the presentation-aware
# service. The base provider/failover service remains exported for focused tests
# and low-level tooling.
WatchlistAIService = PresentationWatchlistAIService

__all__ = [
    "ProviderAttempt",
    "WatchlistAIResult",
    "WatchlistAIService",
    "BaseWatchlistAIService",
    "PresentationWatchlistAIService",
    "build_watchlist_context",
    "build_presentation_context",
    "validate_numbers",
    "validate_response",
]
