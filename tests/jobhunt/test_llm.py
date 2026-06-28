"""Tests for the jobhunt.llm package.

All tests are offline — no real Anthropic API calls are made.
"""

from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import MagicMock

import pytest

from jobhunt.llm import (
    AnthropicLLMClient,
    FakeLLMClient,
    GeminiLLMClient,
    LLMError,
    LLMUnavailable,
    build_llm_client_from_env,
    critique_callback,
    resume_callback,
)
from jobhunt.llm.anthropic_client import _redact_pii
from jobhunt.models import JobPosting, UserProfile
from jobhunt.resume_template import build_resume_draft


# ================================================================== helpers


def _make_sdk_stub(response_text: str = "stub response") -> MagicMock:
    """Build a minimal fake Anthropic SDK object whose messages.create()
    returns a stub with ``.content = [stub(text=response_text)]``."""
    content_block = MagicMock()
    content_block.text = response_text

    msg = MagicMock()
    msg.content = [content_block]

    messages = MagicMock()
    messages.create.return_value = msg

    sdk = MagicMock()
    sdk.messages = messages
    return sdk


def _make_gemini_sdk_stub(response_text: str = "stub response") -> MagicMock:
    """Build a minimal fake google-genai SDK object whose
    models.generate_content() returns a stub with ``.text``."""
    response = MagicMock()
    response.text = response_text

    models = MagicMock()
    models.generate_content.return_value = response

    sdk = MagicMock()
    sdk.models = models
    return sdk


def _profile() -> UserProfile:
    return UserProfile(
        user_id="u-1",
        name="Ada Lovelace",
        email="ada@example.com",
        target_roles=["backend engineer"],
        locations=["Remote"],
        skills=["python", "kubernetes", "postgres"],
        experiences=[
            {
                "title": "Senior Backend Engineer",
                "company": "Globex",
                "highlight": "Built distributed Python services on Kubernetes.",
            }
        ],
    )


def _posting() -> JobPosting:
    return JobPosting(
        job_id="j-1",
        source="greenhouse",
        source_id="g-1",
        url="https://example.com/jobs/1",
        title="Senior Backend Engineer",
        company="Acme",
        location="Remote",
        jd_text="Python on Kubernetes. Postgres a plus.",
    )


# ================================================================== FakeLLMClient


class TestFakeLLMClientDict:
    def test_returns_canned_response_by_substring_match(self):
        client = FakeLLMClient(
            {
                ("rewrite", "keyword"): "polished bullet",
                ("summary", ""): "polished summary",
            }
        )
        result = client.complete("You rewrite bullets", "keyword: python, draft: foo")
        assert result == "polished bullet"

    def test_first_matching_key_wins(self):
        client = FakeLLMClient(
            {
                ("rewrite", ""): "first match",
                ("rewrite", "draft"): "second match",
            }
        )
        # Both keys match but the first one should win.
        result = client.complete("You rewrite something", "draft: foo")
        assert result == "first match"

    def test_returns_empty_string_when_no_key_matches(self):
        client = FakeLLMClient({("critique", "score"): "0.8"})
        result = client.complete("You rewrite bullets", "draft: foo")
        assert result == ""

    def test_case_insensitive_matching(self):
        client = FakeLLMClient({("REWRITE", "KEYWORD"): "hit"})
        result = client.complete("you rewrite bullets", "keyword: python")
        assert result == "hit"

    def test_empty_substrings_match_anything(self):
        """Empty key parts act as wildcards."""
        client = FakeLLMClient({("", ""): "wildcard"})
        result = client.complete("any system prompt", "any user message")
        assert result == "wildcard"

    def test_max_tokens_and_model_params_accepted(self):
        client = FakeLLMClient({("", ""): "ok"})
        result = client.complete("sys", "usr", max_tokens=32, model="claude-x")
        assert result == "ok"


class TestFakeLLMClientCallable:
    def test_callable_responder_receives_system_and_user(self):
        received = {}

        def responder(system: str, user: str) -> str:
            received["system"] = system
            received["user"] = user
            return "callable result"

        client = FakeLLMClient(responder)
        result = client.complete("sys prompt", "user message")

        assert result == "callable result"
        assert received["system"] == "sys prompt"
        assert received["user"] == "user message"

    def test_callable_responder_can_raise(self):
        def broken(system, user):
            raise ValueError("oops")

        client = FakeLLMClient(broken)
        with pytest.raises(ValueError, match="oops"):
            client.complete("s", "u")


# ================================================================== _redact_pii


class TestRedactPii:
    def test_masks_email_addresses(self):
        text = "Contact me at alice@example.com for details."
        result = _redact_pii(text)
        assert "<email>" in result
        assert "alice@example.com" not in result

    def test_masks_phone_numbers(self):
        text = "Call me at +1 800 555-1234 any time."
        result = _redact_pii(text)
        assert "<phone>" in result
        assert "555-1234" not in result

    def test_masks_multiple_pii_items(self):
        text = "Email bob@corp.io or call 020 7946 0958."
        result = _redact_pii(text)
        assert "bob@corp.io" not in result
        assert "<email>" in result
        assert "<phone>" in result

    def test_clean_text_passes_through_unchanged(self):
        text = "No PII here at all."
        assert _redact_pii(text) == text


# ================================================================== AnthropicLLMClient


class TestAnthropicLLMClientUnavailable:
    def test_raises_llm_unavailable_when_sdk_missing(self, monkeypatch):
        """Simulate the anthropic package not being installed."""

        original_import = importlib.import_module

        def _fake_import(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("No module named 'anthropic'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "import_module", _fake_import)

        # Remove anthropic from sys.modules if it happened to be cached.
        monkeypatch.delitem(sys.modules, "anthropic", raising=False)

        with pytest.raises(LLMUnavailable, match="anthropic"):
            AnthropicLLMClient()  # no _sdk injection -> triggers lazy import

    def test_llm_unavailable_is_subclass_of_llm_error(self):
        assert issubclass(LLMUnavailable, LLMError)


class TestAnthropicLLMClientHappyPath:
    def test_complete_returns_assistant_text(self):
        sdk = _make_sdk_stub("Hello, world!")
        client = AnthropicLLMClient(_sdk=sdk)
        result = client.complete("system prompt", "user message")
        assert result == "Hello, world!"

    def test_complete_concatenates_multiple_content_blocks(self):
        block_a = MagicMock()
        block_a.text = "Part A. "
        block_b = MagicMock()
        block_b.text = "Part B."

        msg = MagicMock()
        msg.content = [block_a, block_b]

        sdk = MagicMock()
        sdk.messages.create.return_value = msg

        client = AnthropicLLMClient(_sdk=sdk)
        result = client.complete("sys", "usr")
        assert result == "Part A. Part B."

    def test_complete_passes_model_override(self):
        sdk = _make_sdk_stub("ok")
        client = AnthropicLLMClient(_sdk=sdk)
        client.complete("sys", "usr", model="claude-special")
        call_kwargs = sdk.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-special"

    def test_complete_uses_default_model_when_none_passed(self):
        sdk = _make_sdk_stub("ok")
        client = AnthropicLLMClient(_sdk=sdk)
        client.complete("sys", "usr")
        call_kwargs = sdk.messages.create.call_args.kwargs
        assert call_kwargs["model"] == AnthropicLLMClient.DEFAULT_MODEL

    def test_complete_redacts_pii_before_sending(self):
        sdk = _make_sdk_stub("redacted ok")
        client = AnthropicLLMClient(_sdk=sdk)
        client.complete("system", "user email: alice@example.com")
        call_kwargs = sdk.messages.create.call_args.kwargs
        user_content = call_kwargs["messages"][0]["content"]
        assert "alice@example.com" not in user_content
        assert "<email>" in user_content


class TestAnthropicLLMClientErrors:
    def test_wraps_sdk_exception_as_llm_error(self):
        sdk = MagicMock()
        sdk.messages.create.side_effect = RuntimeError("rate limited")

        client = AnthropicLLMClient(_sdk=sdk)
        with pytest.raises(LLMError, match="rate limited"):
            client.complete("sys", "usr")

    def test_wrapped_llm_error_has_original_cause(self):
        sdk = MagicMock()
        original = RuntimeError("timeout")
        sdk.messages.create.side_effect = original

        client = AnthropicLLMClient(_sdk=sdk)
        with pytest.raises(LLMError) as exc_info:
            client.complete("sys", "usr")
        assert exc_info.value.__cause__ is original


# ================================================================== GeminiLLMClient


class TestGeminiLLMClientUnavailable:
    def test_raises_llm_unavailable_when_sdk_missing(self, monkeypatch):
        """Simulate the google-genai package not being installed."""

        original_import = importlib.import_module

        def _fake_import(name, *args, **kwargs):
            if name == "google.genai":
                raise ImportError("No module named 'google.genai'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "import_module", _fake_import)
        monkeypatch.delitem(sys.modules, "google.genai", raising=False)

        with pytest.raises(LLMUnavailable, match="google-genai"):
            GeminiLLMClient()  # no _sdk injection -> triggers lazy import

    def test_llm_unavailable_is_subclass_of_llm_error(self):
        assert issubclass(LLMUnavailable, LLMError)


class TestGeminiLLMClientHappyPath:
    def test_complete_returns_response_text(self):
        sdk = _make_gemini_sdk_stub("Hello, world!")
        client = GeminiLLMClient(_sdk=sdk)
        result = client.complete("system prompt", "user message")
        assert result == "Hello, world!"

    def test_complete_returns_empty_string_when_response_text_is_none(self):
        sdk = _make_gemini_sdk_stub()
        sdk.models.generate_content.return_value.text = None
        client = GeminiLLMClient(_sdk=sdk)
        result = client.complete("sys", "usr")
        assert result == ""

    def test_complete_passes_model_override(self):
        sdk = _make_gemini_sdk_stub("ok")
        client = GeminiLLMClient(_sdk=sdk)
        client.complete("sys", "usr", model="gemini-special")
        call_kwargs = sdk.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == "gemini-special"

    def test_complete_uses_default_model_when_none_passed(self):
        sdk = _make_gemini_sdk_stub("ok")
        client = GeminiLLMClient(_sdk=sdk)
        client.complete("sys", "usr")
        call_kwargs = sdk.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == GeminiLLMClient.DEFAULT_MODEL

    def test_complete_passes_max_tokens_in_config(self):
        sdk = _make_gemini_sdk_stub("ok")
        client = GeminiLLMClient(_sdk=sdk)
        client.complete("sys", "usr", max_tokens=42)
        call_kwargs = sdk.models.generate_content.call_args.kwargs
        assert call_kwargs["config"]["max_output_tokens"] == 42

    def test_complete_redacts_pii_before_sending(self):
        sdk = _make_gemini_sdk_stub("redacted ok")
        client = GeminiLLMClient(_sdk=sdk)
        client.complete("system", "user email: alice@example.com")
        call_kwargs = sdk.models.generate_content.call_args.kwargs
        assert "alice@example.com" not in call_kwargs["contents"]
        assert "<email>" in call_kwargs["contents"]


class TestGeminiLLMClientErrors:
    def test_wraps_sdk_exception_as_llm_error(self):
        sdk = MagicMock()
        sdk.models.generate_content.side_effect = RuntimeError("rate limited")

        client = GeminiLLMClient(_sdk=sdk)
        with pytest.raises(LLMError, match="rate limited"):
            client.complete("sys", "usr")

    def test_wrapped_llm_error_has_original_cause(self):
        sdk = MagicMock()
        original = RuntimeError("timeout")
        sdk.models.generate_content.side_effect = original

        client = GeminiLLMClient(_sdk=sdk)
        with pytest.raises(LLMError) as exc_info:
            client.complete("sys", "usr")
        assert exc_info.value.__cause__ is original


# ================================================================== build_llm_client_from_env


class TestBuildLlmClientFromEnv:
    def test_returns_none_when_no_keys_set(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert build_llm_client_from_env() is None

    def test_prefers_gemini_when_both_keys_set(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "g-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")

        original_import = importlib.import_module

        def _fake_import(name, *args, **kwargs):
            if name == "google.genai":
                mod = MagicMock()
                mod.Client.return_value = _make_gemini_sdk_stub()
                return mod
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "import_module", _fake_import)

        client = build_llm_client_from_env()
        assert isinstance(client, GeminiLLMClient)

    def test_falls_back_to_anthropic_when_only_that_key_set(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")

        original_import = importlib.import_module

        def _fake_import(name, *args, **kwargs):
            if name == "anthropic":
                mod = MagicMock()
                mod.Anthropic.return_value = _make_sdk_stub()
                return mod
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "import_module", _fake_import)

        client = build_llm_client_from_env()
        assert isinstance(client, AnthropicLLMClient)

    def test_falls_back_to_anthropic_when_gemini_sdk_missing(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "g-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")

        original_import = importlib.import_module

        def _fake_import(name, *args, **kwargs):
            if name == "google.genai":
                raise ImportError("no google-genai")
            if name == "anthropic":
                mod = MagicMock()
                mod.Anthropic.return_value = _make_sdk_stub()
                return mod
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "import_module", _fake_import)

        client = build_llm_client_from_env()
        assert isinstance(client, AnthropicLLMClient)


# ================================================================== resume_callback


class TestResumeCallbackRewriteBullet:
    def test_returns_llm_text_for_rewrite_bullet(self):
        client = FakeLLMClient({("rewrite", "keyword"): "LLM polished bullet"})
        cb = resume_callback(client)
        result = cb("rewrite_bullet", {"keyword": "python", "draft": "old bullet"})
        assert result == "LLM polished bullet"

    def test_unknown_action_returns_empty_string(self):
        client = FakeLLMClient({("", ""): "should not appear"})
        cb = resume_callback(client)
        result = cb("unknown_action", {})
        assert result == ""


class TestResumeCallbackSummary:
    def test_returns_llm_text_for_summary(self):
        # The summary system prompt contains "summaries" and the user message
        # contains the company name lowercased.
        client = FakeLLMClient(
            {("summaries", "acme"): "Ada Lovelace is a Python engineer."}
        )
        cb = resume_callback(client)
        result = cb(
            "summary",
            {
                "profile": {"name": "Ada Lovelace", "skills": ["python"]},
                "posting_title": "SWE",
                "posting_company": "Acme",
                "keywords": ["python"],
            },
        )
        assert result == "Ada Lovelace is a Python engineer."

    def test_model_override_passed_to_client(self):
        received = {}

        def responder(system, user):
            return "ok"

        class TrackingClient:
            def complete(self, system, user, *, max_tokens=512, model=None):
                received["model"] = model
                return "ok"

        cb = resume_callback(TrackingClient(), model="claude-opus-4-7")
        cb("rewrite_bullet", {"keyword": "k", "draft": "d"})
        assert received["model"] == "claude-opus-4-7"


class TestResumeCallbackEndToEnd:
    def test_llm_bullet_appears_in_draft(self):
        """Integrate resume_callback with build_resume_draft."""
        client = FakeLLMClient(
            lambda system, user: (
                "Engineered Python services on Kubernetes at scale."
                if "rewrite" in system.lower()
                else "Ada Lovelace is a distributed systems expert."
            )
        )
        cb = resume_callback(client)
        draft = build_resume_draft(
            _profile(),
            _posting(),
            keywords=["python", "kubernetes"],
            llm=cb,
        )
        all_texts = [b.text for b in draft.all_bullets()]
        # Every bullet was rewritten by the LLM.
        assert all(b.rewritten_by_llm for b in draft.all_bullets())
        # The summary was also rewritten.
        assert "Ada Lovelace" in draft.summary
        assert "expert" in draft.summary


# ================================================================== critique_callback


class TestCritiqueCallback:
    def test_parses_valid_json_response(self):
        payload = json.dumps(
            {"score": 0.82, "flags": ["missing_keyword:rust"], "suggestions": ["Add Rust."]}
        )
        client = FakeLLMClient({("", ""): payload})
        cb = critique_callback(client)
        result = cb(
            {
                "posting_jd": "We need Rust experience.",
                "resume_text": "I know Python.",
                "required_keywords": ["rust"],
            }
        )
        assert result["score"] == pytest.approx(0.82)
        assert "missing_keyword:rust" in result["flags"]
        assert "Add Rust." in result["suggestions"]

    def test_returns_parse_error_flag_on_bad_json(self):
        client = FakeLLMClient({("", ""): "not valid json at all!!!"})
        cb = critique_callback(client)
        result = cb(
            {
                "posting_jd": "jd",
                "resume_text": "resume",
                "required_keywords": [],
            }
        )
        assert result["score"] == pytest.approx(0.5)
        assert "llm_parse_error" in result["flags"]
        assert result["suggestions"] == []

    def test_returns_parse_error_flag_on_partial_json(self):
        client = FakeLLMClient({("", ""): '{"score": 0.7'})  # truncated
        cb = critique_callback(client)
        result = cb({"posting_jd": "j", "resume_text": "r", "required_keywords": []})
        assert "llm_parse_error" in result["flags"]

    def test_empty_flags_and_suggestions_when_model_omits_them(self):
        payload = json.dumps({"score": 0.9})
        client = FakeLLMClient({("", ""): payload})
        cb = critique_callback(client)
        result = cb({"posting_jd": "j", "resume_text": "r", "required_keywords": []})
        assert result["score"] == pytest.approx(0.9)
        assert result["flags"] == []
        assert result["suggestions"] == []


class TestDescribeLlmFromEnv:
    """describe_llm_from_env() probes env + installed packages, no API calls."""

    def test_none_when_no_keys(self, monkeypatch):
        from jobhunt.llm.factory import describe_llm_from_env
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        d = describe_llm_from_env()
        assert d == {"active": False, "provider": None, "model": None, "reason": ""}

    def test_gemini_active_when_key_and_pkg_present(self, monkeypatch):
        import importlib.util
        from jobhunt.llm import factory
        monkeypatch.setenv("GEMINI_API_KEY", "x")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(
            factory.importlib.util, "find_spec",
            lambda name: object() if name == "google.genai" else
            importlib.util.find_spec(name),
        )
        d = factory.describe_llm_from_env()
        assert d["active"] is True and d["provider"] == "gemini" and d["model"]

    def test_gemini_key_but_pkg_missing_reports_reason(self, monkeypatch):
        from jobhunt.llm import factory
        monkeypatch.setenv("GEMINI_API_KEY", "x")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(factory.importlib.util, "find_spec", lambda name: None)
        d = factory.describe_llm_from_env()
        assert d["active"] is False and d["provider"] == "gemini"
        assert "google-genai" in d["reason"]
