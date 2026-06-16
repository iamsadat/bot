"""Offline heuristic enrichers.

These derive plausible signal from the text of a JobPosting — no network
calls.  They implement the :class:`~jobhunt.enrichers.base.Enricher`
protocol and can be replaced by real network-backed enrichers later without
changing the VettingAgent's interface.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from jobhunt.enrichers.base import EnrichmentSignal

if TYPE_CHECKING:
    from jobhunt.models import JobPosting


# ------------------------------------------------------------------ helpers

def _lower(posting: "JobPosting") -> str:
    """Return lower-cased jd_text (or empty string if absent)."""
    return (posting.jd_text or "").lower()


def _count_phrases(text: str, phrases: list[str]) -> int:
    """Count how many distinct phrases from *phrases* appear in *text*."""
    return sum(1 for p in phrases if p in text)


# -------------------------------------------------------- GlassdoorHeuristic

class GlassdoorHeuristic:
    """Proxy Glassdoor rating from culture-keyword density in the JD."""

    name = "glassdoor_heuristic"

    _POSITIVE_CULTURE = [
        "growth",
        "mentorship",
        "remote",
        "wellness",
        "equity",
        "learning",
        "flexible",
        "work-life balance",
        "collaborative",
        "inclusive",
    ]

    # Unambiguously negative culture signals (per spec).
    _NEGATIVE_CULTURE = [
        "willing to work weekends",
        "ground floor",
        "unlimited pto",   # often used as a red flag in disguise
    ]

    def enrich(self, posting: "JobPosting") -> list[EnrichmentSignal]:
        text = _lower(posting)
        if not text:
            return []

        pos = _count_phrases(text, self._POSITIVE_CULTURE)
        neg = _count_phrases(text, self._NEGATIVE_CULTURE)

        # Normalise: max realistic positives ~5, negatives ~3.
        rating = min(1.0, max(0.0, (pos - neg * 1.5) / 5.0 + 0.5))
        rating = round(rating, 4)

        signals: list[EnrichmentSignal] = [
            EnrichmentSignal(
                enricher=self.name,
                company=posting.company,
                metric="rating",
                value=rating,
                detail=(
                    f"positive_signals={pos}, negative_signals={neg}, "
                    f"derived_rating={rating:.2f}"
                ),
            )
        ]

        if neg > pos:
            signals.append(
                EnrichmentSignal(
                    enricher=self.name,
                    company=posting.company,
                    metric="culture_warning",
                    value=round(min(1.0, neg / 3.0), 4),
                    detail=(
                        f"Negative culture phrases ({neg}) outweigh positives "
                        f"({pos}); possible red flags in JD."
                    ),
                )
            )

        return signals


# ------------------------------------------------------ CrunchbaseHeuristic

class CrunchbaseHeuristic:
    """Proxy funding stage and momentum from JD mentions."""

    name = "crunchbase_heuristic"

    # Stage -> normalised 0..1 value
    _STAGE_MAP: list[tuple[str, float]] = [
        ("post-ipo", 0.9),
        ("public company", 0.9),
        ("nasdaq", 0.9),
        ("nyse", 0.9),
        ("series d", 0.8),
        ("series e", 0.8),
        ("series f", 0.8),
        ("late stage", 0.8),
        ("series c", 0.7),
        ("series b", 0.6),
        ("series a", 0.5),
        ("seed", 0.4),
        ("pre-seed", 0.3),
        ("stealth", 0.2),
    ]

    _MOMENTUM_POSITIVE = [
        "raised", "funding", "expansion", "hypergrowth", "scaling",
        "new market", "launch", "partnership",
    ]
    _MOMENTUM_NEGATIVE = [
        "pivot", "restructure", "rightsiz", "headcount reduction",
    ]

    # Rough mapping: founded year → stage weight (older = more established).
    _FOUNDED_YEAR_RE = re.compile(r"founded\s+in\s+((?:19|20)\d{2})", re.IGNORECASE)

    def enrich(self, posting: "JobPosting") -> list[EnrichmentSignal]:
        text = _lower(posting)
        if not text:
            return []

        # Determine stage.
        stage_value = 0.45  # unknown default: somewhere between Seed and A
        stage_label = "unknown"
        for keyword, value in self._STAGE_MAP:
            if keyword in text:
                stage_value = value
                stage_label = keyword
                break

        # Founded-year heuristic: adjust stage if no explicit stage found.
        if stage_label == "unknown":
            m = self._FOUNDED_YEAR_RE.search(posting.jd_text or "")
            if m:
                year = int(m.group(1))
                age = 2024 - year
                if age >= 15:
                    stage_value = 0.8
                elif age >= 8:
                    stage_value = 0.65
                elif age >= 4:
                    stage_value = 0.55
                else:
                    stage_value = 0.45

        # Momentum signal.
        pos_m = _count_phrases(text, self._MOMENTUM_POSITIVE)
        neg_m = _count_phrases(text, self._MOMENTUM_NEGATIVE)
        momentum = min(1.0, max(0.0, 0.5 + (pos_m - neg_m) * 0.1))
        momentum = round(momentum, 4)

        return [
            EnrichmentSignal(
                enricher=self.name,
                company=posting.company,
                metric="stage",
                value=round(stage_value, 4),
                detail=f"inferred_stage={stage_label!r}, value={stage_value:.2f}",
            ),
            EnrichmentSignal(
                enricher=self.name,
                company=posting.company,
                metric="momentum",
                value=momentum,
                detail=(
                    f"positive_momentum_terms={pos_m}, "
                    f"negative_momentum_terms={neg_m}"
                ),
            ),
        ]


# ------------------------------------------------------------ NewsHeuristic

class NewsHeuristic:
    """Proxy recent news sentiment from JD language."""

    name = "news_heuristic"

    _POSITIVE_NEWS = [
        "announced",
        "raised",
        "launched",
        "partnership",
        "award",
        "record revenue",
        "expansion",
        "milestone",
        "breakthrough",
    ]

    _NEGATIVE_NEWS = [
        "controversy",
        "investigation",
        "lawsuit",
        "regulatory",
        "fine",
        "scandal",
        "violation",
        "settlement",
    ]

    def enrich(self, posting: "JobPosting") -> list[EnrichmentSignal]:
        text = _lower(posting)
        if not text:
            return []

        pos = _count_phrases(text, self._POSITIVE_NEWS)
        neg = _count_phrases(text, self._NEGATIVE_NEWS)

        # Neutral baseline = 0.5; each positive nudges up, each negative down.
        sentiment = min(1.0, max(0.0, 0.5 + (pos - neg * 1.5) * 0.08))
        sentiment = round(sentiment, 4)

        return [
            EnrichmentSignal(
                enricher=self.name,
                company=posting.company,
                metric="recent_news_sentiment",
                value=sentiment,
                detail=(
                    f"positive_news_terms={pos}, negative_news_terms={neg}, "
                    f"sentiment={sentiment:.2f}"
                ),
            )
        ]


# --------------------------------------------------------- LayoffsHeuristic

class LayoffsHeuristic:
    """Heuristic layoff-risk signal derived from JD language.

    HIGH value (close to 1.0) means HIGH risk of layoffs.
    The VettingAgent INVERTS this signal before applying its weight.
    """

    name = "layoffs_heuristic"

    _RISK_PHRASES = [
        "restructure",
        "restructuring",
        "rightsiz",
        "headcount",
        "cost-cutting",
        "cost cutting",
        "workforce reduction",
        "reduction in force",
        "rif",
        "downsizing",
    ]

    def enrich(self, posting: "JobPosting") -> list[EnrichmentSignal]:
        text = _lower(posting)
        if not text:
            return []

        hits = _count_phrases(text, self._RISK_PHRASES)
        # Cap at 3 hits for a max risk of 1.0.
        risk = min(1.0, hits / 3.0)
        risk = round(risk, 4)

        return [
            EnrichmentSignal(
                enricher=self.name,
                company=posting.company,
                metric="recent_layoff_risk",
                value=risk,
                detail=(
                    f"layoff_risk_phrases_found={hits}, "
                    f"risk={risk:.2f} (higher = worse; inverted by vetting agent)"
                ),
            )
        ]
