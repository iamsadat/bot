"""Picks an ``LLMClient`` from environment variables, if any are set.

``GEMINI_API_KEY`` is checked first since Gemini's free tier is the
recommended $0 option for solo testing; ``ANTHROPIC_API_KEY`` is the
fallback for the paid Sonnet 4.6 / Opus 4.7 path. With neither, a signed-in
Claude Code CLI on PATH runs prompts on the owner's Claude subscription — see
``claude_code_client``. ``JOBHUNT_LLM`` (claude-code | gemini | anthropic |
none) forces a choice. None is required: callers get ``None`` and the pipeline
stays on deterministic heuristics.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from jobhunt.llm.anthropic_client import AnthropicLLMClient, LLMClient, LLMUnavailable
from jobhunt.llm.claude_code_client import ClaudeCodeLLMClient
from jobhunt.llm.gemini_client import GeminiLLMClient


def _preferred() -> str:
    return os.environ.get("JOBHUNT_LLM", "").strip().lower()


def _claude_code_on_path() -> bool:
    import shutil
    # Never auto-detected offline, so the test suite cannot spend real usage.
    return os.environ.get("JOBHUNT_OFFLINE") != "1" and shutil.which("claude") is not None


def describe_llm_from_env() -> dict:
    """Report which LLM (if any) the environment will use — without API calls.

    Mirrors ``build_llm_client_from_env``'s precedence (Gemini → Anthropic) but
    only inspects env vars + installed packages, so it's cheap enough to call
    from the status endpoint. Returns ``{active, provider, model, reason}``.
    """
    pref = _preferred()
    if pref == "none":
        return {"active": False, "provider": None, "model": None, "reason": ""}
    if pref == "claude-code":
        if _claude_code_on_path() or os.environ.get("JOBHUNT_OFFLINE") == "1":
            return {"active": True, "provider": "claude-code",
                    "model": ClaudeCodeLLMClient.DEFAULT_MODEL, "reason": ""}
        return {"active": False, "provider": "claude-code", "model": None,
                "reason": "Claude Code not found: npm install -g @anthropic-ai/claude-code, "
                          "then run `claude` once to sign in"}
    if os.environ.get("GEMINI_API_KEY") and pref in ("", "gemini"):
        if importlib.util.find_spec("google.genai"):
            return {"active": True, "provider": "gemini",
                    "model": GeminiLLMClient.DEFAULT_MODEL, "reason": ""}
        return {"active": False, "provider": "gemini", "model": None,
                "reason": "google-genai not installed (pip install google-genai)"}
    if os.environ.get("ANTHROPIC_API_KEY") and pref in ("", "anthropic"):
        if importlib.util.find_spec("anthropic"):
            return {"active": True, "provider": "anthropic",
                    "model": AnthropicLLMClient.DEFAULT_MODEL, "reason": ""}
        return {"active": False, "provider": "anthropic", "model": None,
                "reason": "anthropic not installed (pip install anthropic)"}
    if pref == "" and _claude_code_on_path():
        return {"active": True, "provider": "claude-code",
                "model": ClaudeCodeLLMClient.DEFAULT_MODEL, "reason": ""}
    return {"active": False, "provider": None, "model": None, "reason": ""}


def build_llm_client_from_env() -> LLMClient | None:
    pref = _preferred()
    if pref == "none":
        return None
    if pref == "claude-code" or (
            pref == "" and not os.environ.get("GEMINI_API_KEY")
            and not os.environ.get("ANTHROPIC_API_KEY") and _claude_code_on_path()):
        try:
            return ClaudeCodeLLMClient()
        except LLMUnavailable as exc:
            print(f"Claude Code unavailable: {exc}", file=sys.stderr)
            return None

    gemini_key = os.environ.get("GEMINI_API_KEY") if pref in ("", "gemini") else None
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

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") if pref in ("", "anthropic") else None
    if anthropic_key:
        try:
            client = AnthropicLLMClient(api_key=anthropic_key)
            print("LLM tone-polish: Anthropic active (paid).", file=sys.stderr)
            return client
        except LLMUnavailable as exc:
            print(f"ANTHROPIC_API_KEY is set but unusable: {exc}", file=sys.stderr)

    return None


def light_model_for(client) -> str | None:
    """The cheaper model for small, guarded edits (résumé bullets, summary).

    ``JOBHUNT_LLM_MODEL_LIGHT`` overrides; ``same`` uses the client's default.
    Haiku on Claude: these edits are checked line by line and reverted when
    they drift, so they do not need the larger model, and on a Claude plan a
    Haiku call uses far less of the usage limit.
    """
    choice = os.environ.get("JOBHUNT_LLM_MODEL_LIGHT", "").strip()
    if choice.lower() == "same":
        return None
    if choice:
        return choice
    from jobhunt.llm.anthropic_client import AnthropicLLMClient
    from jobhunt.llm.claude_code_client import ClaudeCodeLLMClient

    if isinstance(client, ClaudeCodeLLMClient):
        return "haiku"
    if isinstance(client, AnthropicLLMClient):
        return "claude-haiku-4-5"
    return None
