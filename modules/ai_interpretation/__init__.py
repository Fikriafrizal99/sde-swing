"""AI interpretation helpers for SDE Swing.

Legacy report AI remains presentation-only. Final Watchlist AI now owns a
separate downstream subsystem under ``modules.ai_interpretation.watchlist`` and
must not be executed through this compatibility facade.
"""

from .gemini_interpreter import GeminiInterpreter as _LegacyGeminiInterpreter, InterpretationResult
from .groq_interpreter import GroqHTTPError, GroqInterpreter


class GeminiInterpreter(_LegacyGeminiInterpreter):
    """Backward-compatible import name while production defaults to Groq.

    Existing tests/tools that explicitly configure Gemini keep the legacy
    implementation. A bare ``GeminiInterpreter()`` call, which is what the
    shared report builder uses, returns Groq for the remaining legacy report-AI
    use cases but disables its Final Watchlist call budget. Final Watchlist AI
    is executed only by the isolated downstream watchlist subsystem.
    """

    def __new__(cls, *args, **kwargs):
        if cls is GeminiInterpreter and not args and not kwargs:
            return GroqInterpreter(max_watchlist_calls=0)
        return super().__new__(cls)


__all__ = [
    "GeminiInterpreter",
    "GroqHTTPError",
    "GroqInterpreter",
    "InterpretationResult",
]
