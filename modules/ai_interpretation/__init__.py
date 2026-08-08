"""AI interpretation helpers for SDE Swing.

AI is presentation-only: it may summarize validated engine facts but must never
change decisions, prices, scores, regime, or source status.
"""

from .gemini_interpreter import GeminiInterpreter as _LegacyGeminiInterpreter, InterpretationResult
from .groq_interpreter import GroqHTTPError, GroqInterpreter


class GeminiInterpreter(_LegacyGeminiInterpreter):
    """Backward-compatible import name while production defaults to Groq.

    Existing tests/tools that explicitly configure Gemini keep the legacy
    implementation. A bare ``GeminiInterpreter()`` call, which is what the
    production report builder uses, now returns ``GroqInterpreter`` instead.
    This avoids changing engine/report code while the provider migration is
    rolled out safely.
    """

    def __new__(cls, *args, **kwargs):
        if cls is GeminiInterpreter and not args and not kwargs:
            return GroqInterpreter()
        return super().__new__(cls)


__all__ = [
    "GeminiInterpreter",
    "GroqHTTPError",
    "GroqInterpreter",
    "InterpretationResult",
]
