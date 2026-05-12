"""JobSource protocol — the contract every adapter implements."""

from __future__ import annotations

from typing import Protocol

from jobhunt.models import JobPosting


class SourceUnavailable(Exception):
    """Raised when a source can't be reached. The tool wrapper turns this
    into a degraded result rather than failing the whole batch."""


class JobSource(Protocol):
    name: str

    def search(self, query: dict) -> list[JobPosting]:
        """Return raw, un-deduplicated postings matching ``query``.

        Adapters MUST raise :class:`SourceUnavailable` on transient
        errors so the resilience wrapper can mark the source degraded.
        """
        ...
