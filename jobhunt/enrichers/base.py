"""Enricher protocol and EnrichmentSignal dataclass.

Defines the interface that all enrichers (heuristic or network-backed) must
satisfy.  Deliberately stdlib-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from jobhunt.models import JobPosting


@dataclass
class EnrichmentSignal:
    """A single scalar signal produced by an enricher."""

    enricher: str          # e.g. "glassdoor_heuristic"
    company: str
    metric: str            # e.g. "rating", "recent_layoff_risk"
    value: float           # normalised to 0..1
    detail: str = field(default="")  # human-readable rationale


@runtime_checkable
class Enricher(Protocol):
    """Protocol every enricher must satisfy."""

    name: str

    def enrich(self, posting: "JobPosting") -> list[EnrichmentSignal]:
        """Return zero or more signals derived from *posting*.

        Must be a pure function — no network, no side-effects.
        Must return an empty list when ``posting.jd_text`` is empty/absent.
        """
        ...
