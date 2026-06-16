"""Tests for jobhunt.log — structured JSON logger with PII redaction."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

import pytest

from jobhunt.log import FileSink, StructuredLogger, StdoutSink, redact


# ======================================================================= redact


def test_redact_masks_email() -> None:
    result = redact("Contact me at alice@example.com for details.")
    assert "alice@example.com" not in result
    assert "<email>" in result


def test_redact_masks_phone() -> None:
    result = redact("Call +1 800 555-1234 today.")
    assert "555" not in result or "<phone>" in result
    assert "<phone>" in result


def test_redact_masks_ssn() -> None:
    result = redact("SSN: 123-45-6789.")
    assert "123-45-6789" not in result
    assert "<ssn>" in result


def test_redact_masks_credit_card() -> None:
    result = redact("Card: 4111 1111 1111 1111.")
    assert "4111" not in result
    assert "<card>" in result


def test_redact_credit_card_with_dashes() -> None:
    result = redact("Card: 4111-1111-1111-1111")
    assert "<card>" in result


def test_redact_leaves_non_pii_text() -> None:
    clean = "The quick brown fox jumps over the lazy dog."
    assert redact(clean) == clean


def test_redact_leaves_numbers_that_are_not_pii() -> None:
    # A 4-digit number should NOT be redacted as a card
    assert redact("ticket #1234") == "ticket #1234"


def test_redact_masks_multiple_patterns_in_one_string() -> None:
    text = "Email alice@acme.io and call +44 20 7946 0958"
    result = redact(text)
    assert "<email>" in result
    assert "<phone>" in result
    assert "alice" not in result


# =================================================================== Sink helpers


class _CaptureSink:
    """Test sink that collects lines in a list."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, line: str) -> None:
        self.lines.append(line)


# ================================================================ StructuredLogger


def test_logger_info_emits_json_line() -> None:
    sink = _CaptureSink()
    log = StructuredLogger("test", sinks=[sink])
    log.info("job.found", count=3)

    assert len(sink.lines) == 1
    payload: dict[str, Any] = json.loads(sink.lines[0])
    assert payload["event"] == "job.found"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"
    assert "ts" in payload
    assert payload["count"] == 3


def test_logger_redacts_string_fields() -> None:
    sink = _CaptureSink()
    log = StructuredLogger("test", sinks=[sink])
    log.info("user.login", email="bob@example.com")

    payload = json.loads(sink.lines[0])
    assert payload["email"] == "<email>"


def test_logger_redacts_nested_dict_fields() -> None:
    sink = _CaptureSink()
    log = StructuredLogger("test", sinks=[sink])
    log.info("profile.update", user={"email": "carol@domain.org", "age": 30})

    payload = json.loads(sink.lines[0])
    assert payload["user"]["email"] == "<email>"
    # Non-string values pass through untouched
    assert payload["user"]["age"] == 30


def test_logger_redacts_list_fields() -> None:
    sink = _CaptureSink()
    log = StructuredLogger("test", sinks=[sink])
    log.info("contacts", emails=["a@x.com", "b@y.com"])

    payload = json.loads(sink.lines[0])
    for item in payload["emails"]:
        assert item == "<email>"


def test_file_sink_writes_to_file() -> None:
    with tempfile.NamedTemporaryFile(mode="r", suffix=".jsonl", delete=False) as fh:
        path = fh.name
    try:
        sink = FileSink(path)
        log = StructuredLogger("filesink-test", sinks=[sink])
        log.info("written", key="value")

        with open(path) as f:
            content = f.read().strip()
        payload = json.loads(content)
        assert payload["event"] == "written"
        assert payload["key"] == "value"
    finally:
        os.unlink(path)


def test_multiple_sinks_fan_out() -> None:
    sink_a = _CaptureSink()
    sink_b = _CaptureSink()
    log = StructuredLogger("fanout", sinks=[sink_a, sink_b])
    log.info("ping")

    assert len(sink_a.lines) == 1
    assert len(sink_b.lines) == 1
    assert sink_a.lines[0] == sink_b.lines[0]


def test_logger_level_filtering_drops_debug() -> None:
    sink = _CaptureSink()
    # Default level is INFO — debug messages should be silently dropped
    log = StructuredLogger("filter-test", level=logging.INFO, sinks=[sink])
    log.debug("this should not appear")
    log.info("this should appear")

    assert len(sink.lines) == 1
    payload = json.loads(sink.lines[0])
    assert payload["event"] == "this should appear"


def test_logger_debug_level_passes_debug_messages() -> None:
    sink = _CaptureSink()
    log = StructuredLogger("debug-test", level=logging.DEBUG, sinks=[sink])
    log.debug("low level message", x=1)

    assert len(sink.lines) == 1
    payload = json.loads(sink.lines[0])
    assert payload["level"] == "DEBUG"


def test_logger_warn_and_error_levels() -> None:
    sink = _CaptureSink()
    log = StructuredLogger("levels", sinks=[sink])
    log.warn("something odd")
    log.error("something broke")

    assert len(sink.lines) == 2
    levels = [json.loads(l)["level"] for l in sink.lines]
    assert levels == ["WARNING", "ERROR"]
