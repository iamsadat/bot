"""The Claude-subscription LLM client, driven through a fake subprocess."""

from __future__ import annotations

import json
import subprocess

import pytest

from jobhunt.llm.anthropic_client import LLMError, LLMUnavailable
from jobhunt.llm.claude_code_client import ClaudeCodeLLMClient


class _Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _client(reply, **proc):
    seen = {}

    def run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return _Proc(stdout=json.dumps(reply), **proc)

    return ClaudeCodeLLMClient(executable="/bin/claude", _run=run), seen


def test_returns_the_reply_and_runs_text_only():
    client, seen = _client({"result": "A tailored letter.", "is_error": False})
    out = client.complete("Be grounded.", "Mail me at a@b.com or +91 98765 43210")
    assert out == "A tailored letter."
    cmd = seen["cmd"]
    assert cmd[:2] == ["/bin/claude", "-p"]
    assert cmd[cmd.index("--tools") + 1] == ""  # no file, shell or web access
    assert "--no-session-persistence" in cmd
    # PII never leaves the machine, in the prompt or the system prompt.
    assert "a@b.com" not in seen["input"] and "98765" not in seen["input"]
    # A stray API key must not divert usage away from the subscription.
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert seen["env"]["MAX_THINKING_TOKENS"] == "0"  # no hidden thinking tokens


def test_errors_surface_as_llm_errors():
    client, _ = _client({"result": "Usage limit reached", "is_error": True})
    with pytest.raises(LLMError, match="Usage limit"):
        client.complete("s", "u")

    def timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    with pytest.raises(LLMError, match="timed out"):
        ClaudeCodeLLMClient(executable="/bin/claude", _run=timeout).complete("s", "u")


def test_missing_cli_is_unavailable(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(LLMUnavailable, match="npm install"):
        ClaudeCodeLLMClient()


def test_factory_picks_claude_code_when_asked(monkeypatch):
    from jobhunt.llm import factory
    monkeypatch.setenv("JOBHUNT_LLM", "claude-code")
    monkeypatch.setattr("shutil.which", lambda name: "/bin/claude")
    assert isinstance(factory.build_llm_client_from_env(), ClaudeCodeLLMClient)
    monkeypatch.setenv("JOBHUNT_LLM", "none")
    assert factory.build_llm_client_from_env() is None


def test_offline_tests_never_auto_detect_the_real_cli(monkeypatch):
    from jobhunt.llm import factory
    monkeypatch.delenv("JOBHUNT_LLM", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/bin/claude")
    assert factory.build_llm_client_from_env() is None  # conftest pins OFFLINE=1


def test_a_sweep_stops_calling_the_llm_at_its_budget(monkeypatch):
    from jobhunt.dashboard.server import _sweep_budget
    monkeypatch.setenv("JOBHUNT_LLM_CALLS_PER_SWEEP", "2")
    calls = []
    cb = _sweep_budget(lambda action, payload: calls.append(action) or "text")
    assert [cb("summary", {}) for _ in range(4)] == ["text", "text", "", ""]
    assert len(calls) == 2
