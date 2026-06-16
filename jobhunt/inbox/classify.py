"""Message classifier for the inbox pipeline.

Provides a richer ``classify_message`` that returns a ``Classification``
dataclass with a label, confidence score, and the matched hint strings.

The lexicons are imported directly from ``jobhunt.agents.tracking`` so
the two classifiers stay in sync without duplicating definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jobhunt.agents.tracking import (
    _OFFER_HINTS,
    _REJECT_HINTS,
    _INTERVIEW_HINTS,
    _ASSESSMENT_HINTS,
)


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------

@dataclass
class Classification:
    label: str                   # 'offer' | 'rejection' | 'interview' | 'assessment' | 'other'
    confidence: float            # 0..1
    matched_hints: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

_LABEL_HINTS: dict[str, tuple[str, ...]] = {
    "offer":      _OFFER_HINTS,
    "rejection":  _REJECT_HINTS,
    "interview":  _INTERVIEW_HINTS,
    "assessment": _ASSESSMENT_HINTS,
}

# Priority order: same as tracking.classify so results are consistent.
_LABEL_ORDER = ("offer", "rejection", "assessment", "interview")


def classify_message(subject: str, body: str) -> Classification:
    """Classify a message and return a ``Classification`` with confidence.

    Confidence = ``matched_hint_count / total_hints_in_winning_label``,
    capped at 1.0.  If no label matches, returns ``'other'`` with 0.0.
    """
    text = (subject + " " + body).lower()

    best_label: str = "other"
    best_matched: list[str] = []
    best_confidence: float = 0.0

    for label in _LABEL_ORDER:
        hints = _LABEL_HINTS[label]
        matched = [h for h in hints if h in text]
        if matched:
            confidence = min(1.0, len(matched) / len(hints))
            # First-matching label (highest priority) wins, like tracking.classify
            best_label = label
            best_matched = matched
            best_confidence = confidence
            break

    if best_label == "other":
        return Classification(label="other", confidence=0.0, matched_hints=[])

    return Classification(
        label=best_label,
        confidence=best_confidence,
        matched_hints=best_matched,
    )
