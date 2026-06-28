"""Picks an ``LLMClient`` from environment variables, if any are set.

``GEMINI_API_KEY`` is checked first since Gemini's free tier is the
recommended $0 option for solo testing; ``ANTHROPIC_API_KEY`` is the
fallback for the paid Sonnet 4.6 / Opus 4.7 path. Neither is required —
callers get ``None`` and the pipeline stays on deterministic heuristics.
"""

from __future__ import annotations

import os
import sys

from jobhunt.llm.anthropic_client import AnthropicLLMClient, LLMClient, LLMUnavailable
from jobhunt.llm.gemini_client import GeminiLLMClient


def build_llm_client_from_env() -> LLMClient | None:
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        try:
            return GeminiLLMClient(api_key=gemini_key)
        except LLMUnavailable as exc:
            print(f"GEMINI_API_KEY is set but unusable: {exc}", file=sys.stderr)

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            return AnthropicLLMClient(api_key=anthropic_key)
        except LLMUnavailable as exc:
            print(f"ANTHROPIC_API_KEY is set but unusable: {exc}", file=sys.stderr)

    return None
