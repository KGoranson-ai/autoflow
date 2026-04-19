"""
Compatibility import for source-tree desktop modules.

The canonical TypingEngine implementation lives in
``autoflow_engine.typing_engine`` so the desktop app and published package use
the same behavior.
"""

from autoflow_engine.typing_engine import TypingConfig, TypingEngine

__all__ = ["TypingConfig", "TypingEngine"]
