"""Public surface of the ``jobhunt.llm`` package.

The ``anthropic`` SDK is optional — importing this package never fails even
when the SDK is absent.  ``LLMUnavailable`` is raised only when you attempt
to construct ``AnthropicLLMClient`` without the package installed.
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

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMUnavailable",
    "AnthropicLLMClient",
    "FakeLLMClient",
    "resume_callback",
    "critique_callback",
]
