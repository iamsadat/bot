"""jobhunt.enrichers — company-signal enrichers for the VettingAgent.

Public API
----------
- :class:`Enricher`           — protocol all enrichers implement
- :class:`EnrichmentSignal`   — single scalar signal from an enricher
- :class:`GlassdoorHeuristic` — offline culture/rating proxy
- :class:`CrunchbaseHeuristic`— offline funding-stage proxy
- :class:`NewsHeuristic`      — offline news-sentiment proxy
- :class:`LayoffsHeuristic`   — offline layoff-risk proxy
- :func:`all_heuristics`      — convenience: list of all four heuristic instances
"""

from jobhunt.enrichers.base import Enricher, EnrichmentSignal
from jobhunt.enrichers.heuristic import (
    CrunchbaseHeuristic,
    GlassdoorHeuristic,
    LayoffsHeuristic,
    NewsHeuristic,
)


def all_heuristics() -> list[Enricher]:
    """Return one fresh instance of each of the four heuristic enrichers."""
    return [
        GlassdoorHeuristic(),
        CrunchbaseHeuristic(),
        NewsHeuristic(),
        LayoffsHeuristic(),
    ]


__all__ = [
    "Enricher",
    "EnrichmentSignal",
    "GlassdoorHeuristic",
    "CrunchbaseHeuristic",
    "NewsHeuristic",
    "LayoffsHeuristic",
    "all_heuristics",
]
