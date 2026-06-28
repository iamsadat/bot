"""Gemini adapter for ``LLMClient`` — a free-tier alternative to Anthropic.

The ``google-genai`` package is an optional dependency — this module
lazy-imports it inside ``GeminiLLMClient.__init__`` so the rest of the
codebase remains usable when the package is not installed, mirroring
``AnthropicLLMClient`` in ``anthropic_client.py``.
"""

from __future__ import annotations

from jobhunt.llm.anthropic_client import LLMError, LLMUnavailable, _redact_pii


class GeminiLLMClient:
    """Implements the ``LLMClient`` protocol against the Gemini API.

    Gemini's free tier (e.g. ``gemini-3.5-flash``) makes this a $0 option
    for solo testing — get a key at https://aistudio.google.com/apikey.
    """

    DEFAULT_MODEL = "gemini-3.5-flash"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str | None = None,
        _sdk=None,  # injection point for tests
    ) -> None:
        self._default_model = default_model or self.DEFAULT_MODEL

        if _sdk is not None:
            # Test-injected SDK object (e.g. a mock).
            self._client = _sdk
        else:
            try:
                import importlib
                genai_mod = importlib.import_module("google.genai")
                self._client = genai_mod.Client(
                    **({"api_key": api_key} if api_key else {})
                )
            except ImportError as exc:
                raise LLMUnavailable(
                    "The 'google-genai' package is not installed. "
                    "Install it with: pip install google-genai"
                ) from exc

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 512,
        model: str | None = None,
    ) -> str:
        """Send a message and return the model's text output."""
        system = _redact_pii(system)
        user = _redact_pii(user)
        effective_model = model or self._default_model
        try:
            response = self._client.models.generate_content(
                model=effective_model,
                contents=user,
                config={"system_instruction": system, "max_output_tokens": max_tokens},
            )
            return response.text or ""
        except LLMUnavailable:
            raise
        except Exception as exc:
            raise LLMError(f"Gemini API call failed: {exc}") from exc
