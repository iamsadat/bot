"""Seniority levels, read off job titles and off the candidate's own history.

Nothing in the product used to read seniority at all, which is why a candidate
with under a year of experience was shown Staff and Principal roles and had no
way to tell they were out of reach. Titles are the only signal every board
agrees on — salary is usually absent and the "N+ years" line in a JD is prose —
so this module keeps to titles plus the candidate's own date ranges.

Levels are a small ordered scale, deliberately coarse:

    0 intern · 1 junior · 2 mid · 3 senior · 4 staff/lead · 5 director+

``None`` means *unknown*, and callers must treat it as neutral rather than
guessing. Well over half of the titles on a real board carry no level marker
("Data Platform Engineer"), and silently filing those under "mid" would either
hide good jobs or wave through bad ones depending on the reader's level.
"""

from __future__ import annotations

import re
from typing import Any

INTERN, JUNIOR, MID, SENIOR, STAFF, DIRECTOR = 0, 1, 2, 3, 4, 5

LEVEL_NAMES: dict[int, str] = {
    INTERN: "intern",
    JUNIOR: "junior",
    MID: "mid",
    SENIOR: "senior",
    STAFF: "staff/lead",
    DIRECTOR: "director+",
}

# Matched against the title with word boundaries. German terms are here because
# Arbeitnow is one of the default sources and its board is largely German.
_MARKERS: dict[int, tuple[str, ...]] = {
    INTERN: (
        "intern", "internship", "trainee", "apprentice", "praktikum",
        "praktikant", "werkstudent", "working student", "ausbildung",
    ),
    JUNIOR: (
        "junior", "jr", "entry level", "entry-level", "graduate", "grad",
        "fresher", "campus", "einsteiger", "berufseinsteiger",
    ),
    MID: ("mid", "midweight", "mid-level", "intermediate"),
    SENIOR: ("senior", "sr", "erfahren"),
    STAFF: (
        "staff", "principal", "lead", "leader", "leiter", "architect",
        "distinguished", "fellow",
    ),
    DIRECTOR: (
        "director", "head of", "vp", "vice president", "chief", "cto", "cio",
        "manager", "managing", "president",
    ),
}

_MARKER_RES: tuple[tuple[int, re.Pattern[str]], ...] = tuple(
    (
        level,
        re.compile(
            r"(?<![a-z])(?:%s)(?![a-z])" % "|".join(re.escape(w) for w in words),
            re.I,
        ),
    )
    for level, words in _MARKERS.items()
)


def level_from_title(title: str | None) -> int | None:
    """Best-guess level for a job title, or ``None`` when it carries no marker.

    Takes the *highest* marker present, which is what resolves the compound
    titles boards actually publish: "Senior Staff Engineer" is above Staff, and
    "Junior Data Engineer (Former Data Engineering Intern)" is a junior role
    rather than an internship.
    """
    if not title:
        return None
    found = [level for level, rx in _MARKER_RES if rx.search(title)]
    return max(found) if found else None


# Experience written into the title itself: "(2-4 years)", "(4+ YOE)",
# "Big Data (7 to 11 years)". Extremely common on Indian job boards, and often
# the only level signal a title carries.
_YEARS_RE = re.compile(
    # Decimals and a no-mid-number start: "7.1-9 years" used to read as 1, and
    # "2.5 - 4 years" as 5, turning a senior role junior.
    r"(?<![\d.])(\d{1,2}(?:\.\d+)?)\s*(?:\+|\s*(?:to|-|–|—)\s*\d{1,2}(?:\.\d+)?\+?)?"
    r"\s*(?:years?|yrs?|yoe)\b",
    re.I,
)


# Numeric career ladders: "Software Engineer 3", "Software Engineer (L1)",
# "Software Development Engineer III", "SDE IV". Widely used and, without it,
# an SDE III read as unmarked and scored 100% for a candidate with one year.
# Lone "I" is deliberately excluded — far too ambiguous in a job title.
_RANK_RE = re.compile(
    r"(?:\bL(?P<l>[1-6])\b"
    r"|\b(?P<roman>IV|V|III|II)\b"
    r"|[(\-–—,]\s*(?P<paren>[1-6])\s*[)\s]?\s*$"
    r"|\s(?P<trailing>[1-6])$)"
)

_ROMAN = {"II": 2, "III": 3, "IV": 4, "V": 5}

# Ladder rung → level. L1 is an entry hire, L3 is where "senior" usually starts.
_RANK_LEVEL = {1: JUNIOR, 2: MID, 3: SENIOR, 4: STAFF, 5: STAFF, 6: DIRECTOR}


def rank_in_title(title: str | None) -> int | None:
    """Level implied by a numeric or roman ladder rung in the title."""
    if not title:
        return None
    m = _RANK_RE.search(title)
    if not m:
        return None
    if m.group("roman"):
        rung = _ROMAN[m.group("roman")]
    else:
        rung = int(m.group("l") or m.group("paren") or m.group("trailing"))
    return _RANK_LEVEL.get(rung)


def years_in_title(title: str | None) -> int | None:
    """Lowest years-of-experience figure stated in a title, if any.

    The *lowest* because a range is a floor plus room: "4 to 8 years" is open to
    somebody with four. Titles only — hunting the same pattern through a job
    description picks up company history and benefit tenures.
    """
    if not title:
        return None
    values = [int(float(m.group(1))) for m in _YEARS_RE.finditer(title)]
    return min(values) if values else None


def level_from_posting(posting: Any) -> int | None:
    """A posting's level, preferring declared data over anything inferred.

    Four signals in falling order of trust: what the board declares (Himalayas'
    ``seniority``, copied by adapters into ``raw["seniority"]``), a level word in
    the title, years of experience stated in the title, then a numeric ladder
    rung. A posting open to several levels is treated as reachable at the lowest
    of them — "Entry-level, Mid-level" is an entry-level opportunity.
    """
    raw = getattr(posting, "raw", None) or {}
    declared = raw.get("seniority")
    if declared:
        values = declared if isinstance(declared, (list, tuple)) else [declared]
        levels = [lvl for v in values if (lvl := level_from_title(str(v))) is not None]
        if levels:
            return min(levels)

    title = getattr(posting, "title", "")
    marked = level_from_title(title)
    if marked is not None:
        return marked
    # Years before rank: "(3-5 Years)" is a clearer statement than any numeral
    # that happens to sit at the end of a title.
    from_years = level_from_years(years_in_title(title))
    if from_years is not None:
        return from_years
    return rank_in_title(title)


def level_from_years(years: int | float | None) -> int | None:
    """Map years of professional experience onto the scale.

    Never returns ``INTERN`` — an internship is a title, not a duration, and
    somebody with zero completed years may still hold a graduate role.
    """
    if years is None:
        return None
    try:
        y = float(years)
    except (TypeError, ValueError):
        return None
    if y < 0:
        return None
    if y <= 1:
        return JUNIOR
    if y <= 4:
        return MID
    if y <= 8:
        return SENIOR
    if y <= 12:
        return STAFF
    return DIRECTOR


def level_from_profile(profile: Any) -> int | None:
    """The candidate's own level.

    Prefers an explicit setting, then measured years, then title text. Years
    beat titles because ``target_roles`` describes what somebody *wants* rather
    than what they are, and the title fallback takes the **lowest** marked
    target so that adding an aspirational "Senior X" to the list never quietly
    filters out the roles they actually qualify for.
    """
    explicit = getattr(profile, "seniority_level", None)
    if explicit is not None:
        try:
            return max(INTERN, min(DIRECTOR, int(explicit)))
        except (TypeError, ValueError):
            pass

    from_years = level_from_years(getattr(profile, "experience_years", None))
    if from_years is not None:
        return from_years

    marked = [
        lvl
        for text in [getattr(profile, "current_title", "")]
        + list(getattr(profile, "target_roles", []) or [])
        if (lvl := level_from_title(text)) is not None
    ]
    return min(marked) if marked else None


# How much a level gap costs. Keys are ``posting_level - candidate_level``, so
# positive means the posting is above the candidate. Reaching up one rung is a
# reasonable stretch; reaching up three is noise. Being over-levelled for a role
# is a milder problem than being under-levelled, but it is still a poor match.
_GAP_SCORES: dict[int, float] = {
    0: 1.0,
    1: 0.75,
    -1: 0.8,
    2: 0.35,
    -2: 0.5,
}


def fit(candidate_level: int | None, posting_level: int | None) -> float:
    """0..1 score for how well a posting's level suits the candidate.

    Unknown on either side scores 1.0. That is deliberate: an unmarked title is
    not evidence of a mismatch, and penalising it would bury the majority of
    every board behind the minority that spells its level out.
    """
    if candidate_level is None or posting_level is None:
        return 1.0
    return _GAP_SCORES.get(posting_level - candidate_level, 0.1)


def within_band(
    candidate_level: int | None,
    posting_level: int | None,
    up: int = 1,
    down: int = 2,
) -> bool:
    """Whether a posting is close enough in level to be worth showing at all.

    Asymmetric on purpose. One rung up is a stretch worth seeing; two rungs up
    is the "why am I being shown Staff roles" complaint this exists to fix.
    Downward tolerance is looser because a role posted below your level is
    merely unambitious, not unattainable.
    """
    if candidate_level is None or posting_level is None:
        return True
    return -down <= posting_level - candidate_level <= up
