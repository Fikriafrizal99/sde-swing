"""AI interpretation helpers for SDE Swing.

AI is presentation-only: it may summarize validated engine facts but must never
change decisions, prices, scores, regime, or source status.
"""

from .gemini_interpreter import GeminiInterpreter, InterpretationResult

__all__ = ["GeminiInterpreter", "InterpretationResult"]
