"""Structured JSON logger with PII redaction.

Emits one JSON line per event to one or more sinks. All string values in
keyword fields are redacted before serialization.

Usage::

    from jobhunt.log import StructuredLogger, StdoutSink, FileSink

    log = StructuredLogger("myagent", sinks=[StdoutSink()])
    log.info("job.found", title="Engineer", email="user@example.com")
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Protocol


# ------------------------------------------------------------------ patterns

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\+?\d[\d \-()\s]{7,}\d")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD_RE = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")


def redact(text: str) -> str:
    """Mask PII patterns in *text* and return the sanitised string.

    Patterns matched (in order):
    - Credit card (16 digits, optional separators) → ``<card>``
    - SSN (``DDD-DD-DDDD``) → ``<ssn>``
    - Phone (7+ digit sequence, may have spaces/dashes/parens) → ``<phone>``
    - Email → ``<email>``
    """
    # Order matters: card before phone to avoid partial matches
    text = _CARD_RE.sub("<card>", text)
    text = _SSN_RE.sub("<ssn>", text)
    text = _PHONE_RE.sub("<phone>", text)
    text = _EMAIL_RE.sub("<email>", text)
    return text


def _redact_value(value: Any) -> Any:
    """Recursively redact string leaves in *value*."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


# --------------------------------------------------------------------- sinks


class Sink(Protocol):
    """Anything that can receive a single log line."""

    def write(self, line: str) -> None: ...  # pragma: no cover


class StdoutSink:
    """Writes log lines to stdout via ``print``."""

    def write(self, line: str) -> None:
        print(line)


class FileSink:
    """Appends log lines to a file, one line per call."""

    def __init__(self, path: str) -> None:
        self._path = path

    def write(self, line: str) -> None:
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


# ------------------------------------------------------------------- logger


class StructuredLogger:
    """Thin wrapper over stdlib logging that emits JSON lines and redacts PII.

    Parameters
    ----------
    name:
        Logger name (appears as ``"logger"`` in each JSON line).
    level:
        Minimum log level (stdlib constant, e.g. ``logging.DEBUG``).
    sinks:
        List of :class:`Sink` objects that receive each formatted line.
        Defaults to ``[StdoutSink()]``.
    """

    def __init__(
        self,
        name: str,
        *,
        level: int = logging.INFO,
        sinks: list[Sink] | None = None,
    ) -> None:
        self._name = name
        self._level = level
        self._sinks: list[Sink] = sinks if sinks is not None else [StdoutSink()]

    # ----------------------------------------------------------------- public

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, event, **fields)

    def warn(self, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, event, **fields)

    # --------------------------------------------------------------- private

    def _emit(self, level: int, event: str, **fields: Any) -> None:
        if level < self._level:
            return
        payload: dict[str, Any] = {
            "ts": time.time(),
            "level": logging.getLevelName(level),
            "logger": self._name,
            "event": redact(event),
        }
        for key, value in fields.items():
            payload[key] = _redact_value(value)
        line = json.dumps(payload, default=str)
        for sink in self._sinks:
            sink.write(line)
