"""Picks an ``LLMClient`` from environment variables, if any are set.

``GEMINI_API_KEY`` is checked first since Gemini's free tier is the
recommended $0 option for solo testing; ``ANTHROPIC_API_KEY`` is the
fallback for the paid Sonnet 4.6 / Opus 4.7 path. Neither is required —
callers get ``None`` and the pipeline stays on deterministic heuristics.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from jobhunt.llm.anthropic_client import AnthropicLLMClient, LLMClient, LLMUnavailable
from jobhunt.llm.gemini_client import GeminiLLMClient


def describe_llm_from_env() -> dict:
    """Report which LLM (if any) the environment will use — without API calls.

    Mirrors ``build_llm_client_from_env``'s precedence (Gemini → Anthropic) but
    only inspects env vars + installed packages, so it's cheap enough to call
    from the status endpoint. Returns ``{active, provider, model, reason}``.
    """
    if os.environ.get("GEMINI_API_KEY"):
        if importlib.util.find_spec("google.genai"):
            return {"active": True, "provider": "gemini",
                    "model": GeminiLLMClient.DEFAULT_MODEL, "reason": ""}
        return {"active": False, "provider": "gemini", "model": None,
                "reason": "google-genai not installed (pip install google-genai)"}
    if os.environ.get("ANTHROPIC_API_KEY"):
        if importlib.util.find_spec("anthropic"):
            return {"active": True, "provider": "anthropic",
                    "model": AnthropicLLMClient.DEFAULT_MODEL, "reason": ""}
        return {"active": False, "provider": "anthropic", "model": None,
                "reason": "anthropic not installed (pip install anthropic)"}
    return {"active": False, "provider": None, "model": None, "reason": ""}


def build_llm_client_from_env() -> LLMClient | None:
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        try:
            client = GeminiLLMClient(api_key=gemini_key)
            print(
                f"LLM tone-polish: Gemini active ({client.DEFAULT_MODEL}, free tier).",
                file=sys.stderr,
            )
            return client
        except LLMUnavailable as exc:
            print(f"GEMINI_API_KEY is set but unusable: {exc}", file=sys.stderr)

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            client = AnthropicLLMClient(api_key=anthropic_key)
            print("LLM tone-polish: Anthropic active (paid).", file=sys.stderr)
            return client
        except LLMUnavailable as exc:
            print(f"ANTHROPIC_API_KEY is set but unusable: {exc}", file=sys.stderr)

    return None
