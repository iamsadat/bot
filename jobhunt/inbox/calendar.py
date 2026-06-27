"""Calendar hint extractor for the inbox pipeline.

Given a message body, detects scheduling-related content:
- Calendly URLs
- Zoom meeting URLs
- Proposed datetime strings in common formats

Returns a ``CalendarHint`` dataclass.  No datetime parsing is performed —
raw matched substrings are surfaced for the human (or dashboard) to act on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_CALENDLY_RE = re.compile(
    r"https://calendly\.com/[\w%-]+/[\w%-]+",
    re.IGNORECASE,
)

_ZOOM_RE = re.compile(
    r"https://(?:[\w.-]+\.)?zoom\.us/j/\d+",
    re.IGNORECASE,
)

# Common datetime formats:
#   "Monday, March 4 at 2pm EST"
#   "March 4 at 2pm"
#   "3/4 at 14:00"
#   ISO 8601: "2024-03-04T14:00:00"
#   "next Tuesday at 3pm"
_DATETIME_PATTERNS = [
    # ISO 8601
    re.compile(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?",
        re.IGNORECASE,
    ),
    # "Monday, March 4 at 2pm EST" or "March 4 at 2pm"
    re.compile(
        r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+)?"
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+\d{1,2}"
        r"(?:,\s*\d{4})?"
        r"\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:[A-Z]{2,5})?",
        re.IGNORECASE,
    ),
    # "3/4 at 14:00" or "3/4/2024 at 2pm"
    re.compile(
        r"\d{1,2}/\d{1,2}(?:/\d{2,4})?\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?",
        re.IGNORECASE,
    ),
    # "next Tuesday at 3pm" / "this Friday at 10am"
    re.compile(
        r"(?:next|this)\s+"
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
        r"\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:[A-Z]{2,5})?",
        re.IGNORECASE,
    ),
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CalendarHint:
    has_link: bool
    link: str = ""
    proposed_time: str = ""   # raw match — no parsing
    notes: str = ""


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

def extract_calendar(body: str) -> CalendarHint:
    """Scan *body* for scheduling signals and return a ``CalendarHint``."""
    link = ""
    has_link = False

    # Prefer Calendly over Zoom when both present
    m = _CALENDLY_RE.search(body)
    if m:
        link = m.group(0)
        has_link = True
    else:
        m = _ZOOM_RE.search(body)
        if m:
            link = m.group(0)
            has_link = True

    proposed_time = ""
    for pattern in _DATETIME_PATTERNS:
        m = pattern.search(body)
        if m:
            proposed_time = m.group(0).strip()
            break

    return CalendarHint(
        has_link=has_link,
        link=link,
        proposed_time=proposed_time,
    )
