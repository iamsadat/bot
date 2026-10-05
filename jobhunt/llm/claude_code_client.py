"""LLM client that runs on the owner's Claude subscription via Claude Code.

A Claude Pro/Max plan includes no API key, but it does include the Claude Code
CLI, which signs in with the subscription and has a headless mode:
``claude -p``. This client sends each prompt through it with every tool
disabled and nothing saved, so it is a plain text completion billed to the
plan's usage limits rather than to an API account.

Set up once on the machine that runs ``jobhunt me``:

    npm install -g @anthropic-ai/claude-code
    claude            # sign in with your Claude account, then exit
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from jobhunt.llm.anthropic_client import LLMError, LLMUnavailable, _redact_pii


class ClaudeCodeLLMClient:
    """``LLMClient`` over the ``claude`` CLI in headless print mode."""

    DEFAULT_MODEL = "sonnet"  # a Claude Code alias; tracks the current Sonnet

    def __init__(self, executable: str | None = None, default_model: str | None = None,
                 timeout_s: float = 120.0, _run=subprocess.run) -> None:
        self._exe = executable or shutil.which("claude")
        if not self._exe:
            raise LLMUnavailable(
                "The Claude Code CLI is not installed. Install it with "
                "`npm install -g @anthropic-ai/claude-code`, then run `claude` "
                "once to sign in with your Claude subscription.")
        self._default_model = default_model or self.DEFAULT_MODEL
        self._timeout = timeout_s
        self._run = _run

    def complete(self, system: str, user: str, *, max_tokens: int = 512,
                 model: str | None = None) -> str:
        """Return the reply text. ``max_tokens`` is advisory: the CLI has no
        per-call cap, so the prompt carries the length requirement instead."""
        cmd = [
            self._exe, "-p",
            "--output-format", "json",
            "--model", model or self._default_model,
            "--system-prompt", _redact_pii(system),
            "--tools", "",               # text only: no file, shell or web access
            "--no-session-persistence",  # nothing written to the owner's history
            "--setting-sources", "",     # ignore project/user settings and hooks
        ]
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        # Claude Code thinks before answering by default. For these short,
        # checked edits that was ~95% of the output tokens (a 300-token bullet
        # edit came back as ~6,800) and most of the wait.
        env["MAX_THINKING_TOKENS"] = os.environ.get("JOBHUNT_LLM_THINKING_TOKENS", "0")
        try:
            with tempfile.TemporaryDirectory() as cwd:  # no repo to read
                proc = self._run(cmd, input=_redact_pii(user), capture_output=True,
                                 text=True, timeout=self._timeout, cwd=cwd, env=env)
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"claude timed out after {self._timeout:.0f}s") from exc
        except OSError as exc:
            raise LLMError(f"could not run claude: {exc}") from exc
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"claude returned non-JSON (exit {proc.returncode}): "
                f"{(proc.stderr or proc.stdout or '')[:200]}") from exc
        if proc.returncode != 0 or payload.get("is_error"):
            reason = payload.get("result") or proc.stderr or f"exit {proc.returncode}"
            raise LLMError(f"claude failed: {str(reason)[:200]}")
        u = payload.get("usage") or {}
        from jobhunt.llm.cache import usage
        usage.record(
            input_tokens=sum(int(u.get(k) or 0) for k in (
                "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
            output_tokens=int(u.get("output_tokens") or 0))
        return str(payload.get("result") or "")
