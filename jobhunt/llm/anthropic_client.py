"""Thin wrapper around the Anthropic SDK.

The ``anthropic`` package is an optional dependency — this module lazy-imports
it inside ``AnthropicLLMClient.__init__`` so the rest of the codebase remains
usable when the package is not installed.
"""

from __future__ import annotations

import re
from typing import Callable, Protocol, runtime_checkable


# ------------------------------------------------------------------ errors


class LLMError(Exception):
    """Base exception for all LLM-related errors."""


class LLMUnavailable(LLMError):
    """Raised when the Anthropic SDK is not installed or the client cannot
    be initialised."""


# ------------------------------------------------------------------ PII


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\+?\d[\d \-()‐]{7,}\d")


def _redact_pii(text: str) -> str:
    """Replace email addresses and phone numbers with placeholders."""
    text = _EMAIL_RE.sub("<email>", text)
    text = _PHONE_RE.sub("<phone>", text)
    return text


# ------------------------------------------------------------------ protocol


@runtime_checkable
class LLMClient(Protocol):
    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 512,
        model: str | None = None,
    ) -> str:
        ...


# ------------------------------------------------------------------ real client


class AnthropicLLMClient:
    """Real client.  Lazy-imports ``anthropic`` so the package is optional."""

    DEFAULT_MODEL = "claude-sonnet-4-6"
    CRITIQUE_MODEL = "claude-opus-4-7"

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
            # Lazy import — if the package is missing, surface a clear message.
            try:
                import importlib
                anthropic_mod = importlib.import_module("anthropic")
                self._client = anthropic_mod.Anthropic(
                    **({"api_key": api_key} if api_key else {})
                )
            except ImportError as exc:
                raise LLMUnavailable(
                    "The 'anthropic' package is not installed. "
                    "Install it with: pip install anthropic"
                ) from exc

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 512,
        model: str | None = None,
    ) -> str:
        """Send a message and return the concatenated assistant text."""
        system = _redact_pii(system)
        user = _redact_pii(user)
        effective_model = model or self._default_model
        try:
            response = self._client.messages.create(
                model=effective_model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return "".join(block.text for block in response.content)
        except LLMUnavailable:
            raise
        except Exception as exc:
            raise LLMError(f"Anthropic API call failed: {exc}") from exc


# ------------------------------------------------------------------ fake client


class FakeLLMClient:
    """Test double.

    Pass either:
    * A ``dict`` mapping ``(system_substring, user_substring) -> response``.
      Both substrings are matched case-insensitively against the actual args.
      The first matching key wins.  Pass empty strings to match anything.
    * A callable ``(system, user) -> str``.
    """

    def __init__(
        self,
        responder: dict[tuple[str, str], str] | Callable[[str, str], str],
    ) -> None:
        self._responder = responder

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 512,
        model: str | None = None,
    ) -> str:
        if callable(self._responder):
            return self._responder(system, user)

        # Dict look-up: find the first entry whose key substrings both appear
        # in the respective argument (case-insensitive).
        sys_lower = system.lower()
        usr_lower = user.lower()
        for (sys_sub, usr_sub), response in self._responder.items():
            if sys_sub.lower() in sys_lower and usr_sub.lower() in usr_lower:
                return response

        return ""
