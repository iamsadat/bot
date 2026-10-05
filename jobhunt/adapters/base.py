"""JobSource protocol — the contract every adapter implements."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from jobhunt.http import HTTPClientError
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


def fetch_boards(http, urls: dict[str, str], *, workers: int = 8,
                 ) -> list[tuple[str, Any, str | None]]:
    """GET every board at once: ``(key, payload, error)`` in input order.

    A seeded list of 170-odd company boards fetched one at a time made a single
    sweep take minutes. Results keep the input order, so output is unchanged.
    """
    def one(item: tuple[str, str]) -> tuple[str, Any, str | None]:
        key, url = item
        try:
            return key, http.get_json(url), None
        except HTTPClientError as exc:
            return key, None, f"{key}: {exc}"

    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(urls))) as pool:
        return list(pool.map(one, urls.items()))
