"""Public surface of the ``jobhunt.llm`` package.

The ``anthropic`` and ``google-genai`` SDKs are both optional — importing
this package never fails even when neither is installed.  ``LLMUnavailable``
is raised only when you attempt to construct a client without its package.
"""

from jobhunt.llm.anthropic_client import (
    LLMClient,
    LLMError,
    LLMUnavailable,
    AnthropicLLMClient,
    FakeLLMClient,
)
from jobhunt.llm.callbacks import (
    resume_callback,
    critique_callback,
)
from jobhunt.llm.factory import build_llm_client_from_env
from jobhunt.llm.gemini_client import GeminiLLMClient

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMUnavailable",
    "AnthropicLLMClient",
    "GeminiLLMClient",
    "FakeLLMClient",
    "resume_callback",
    "critique_callback",
    "build_llm_client_from_env",
]
