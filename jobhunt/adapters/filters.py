"""Local query filters shared by all real adapters.

Greenhouse / Lever / Ashby public APIs return the *full* board — they
have no native search. We apply the same role / location / remote /
exclude rules client-side so the JobSource protocol stays consistent
with the in-memory FixtureSource.

Matching is deliberately strict about the **title**. An earlier version fell
back to "every word of the role appears somewhere in the job description",
which let a search for "data engineer" return a German tax-advisory listing and
an SMB account-executive role — both descriptions happened to contain the words
"data" and "engineer" in unrelated sentences. A job's title is the only field
that states what the job *is*; the body states what the company is like.
"""

from __future__ import annotations

from jobhunt.models import JobPosting
from jobhunt.seniority import level_from_posting, within_band
from jobhunt.skills_taxonomy import expand_terms

# Title words shared by most jobs in a field. Matching only these means "some
# kind of engineering role", which is not the same as the role being searched
# for, so they cannot carry a match on their own.
_GENERIC = frozenset({
    "engineer", "engineering", "developer", "development", "analyst",
    "manager", "specialist", "consultant", "scientist", "architect",
    "administrator", "associate", "professional", "expert", "officer",
    "coordinator", "technician", "and", "or", "of", "the", "for", "with",
    "in", "at", "a", "an",
})

# Level markers belong to the seniority check, not the title check: a search for
# "data engineer" should still match "Junior Data Engineer".
_LEVELS = frozenset({
    "senior", "junior", "staff", "principal", "lead", "head", "chief",
    "intern", "graduate", "entry", "mid", "midweight", "sr", "jr", "trainee",
    "director", "vp",
})

# Locations that mean "no geographic requirement" rather than a place.
ANYWHERE = frozenset({"remote", "anywhere", "worldwide", "global"})

# Generic words grouped into job families. "Engineer" and "Developer" are
# interchangeable; "Engineer" and "Analyst" are not. Without this, a search for
# "data engineer" matched "Data Analyst" — the only distinctive word, "data",
# was present and the differing job family went unchecked.
_FAMILY: dict[str, str] = {
    "engineer": "eng", "engineering": "eng", "developer": "eng",
    "development": "eng", "programmer": "eng", "sde": "eng",
    "analyst": "analyst", "analytics": "analyst",
    "scientist": "scientist", "science": "scientist",
    "manager": "manager", "management": "manager",
    "architect": "architect", "administrator": "admin",
    "consultant": "consultant", "specialist": "specialist",
    "technician": "technician", "designer": "designer",
}


def _families(tokens: set[str]) -> set[str]:
    return {_FAMILY[t] for t in tokens if t in _FAMILY}


def words(text: str) -> set[str]:
    return {w for w in "".join(
        c if c.isalnum() or c in "+#" else " " for c in (text or "").lower()
    ).split() if w}


# Country shorthands, so a scope of "US" satisfies a candidate in "United
# States". Deliberately tiny — only the forms job boards actually publish.
_PLACE_ALIASES: dict[str, frozenset[str]] = {
    "us": frozenset({"usa", "united", "states", "america"}),
    "usa": frozenset({"us", "united", "states", "america"}),
    "uk": frozenset({"gb", "united", "kingdom", "britain", "england"}),
    "gb": frozenset({"uk", "united", "kingdom", "britain", "england"}),
    "uae": frozenset({"emirates", "dubai"}),
}

# City → (country, Adzuna country code). Job boards routinely post a bare city:
# Indian boards say "Bangalore" or "Hybrid in Bengaluru", never "Bangalore,
# India". Without this, a candidate who listed "Hyderabad, India" matched *none*
# of 128 India-based roles on the seeded boards — 201 of 202 postings were
# filtered out purely for want of knowing that Bengaluru is in India.
#
# Kept to the cities these boards actually use. It is a lookup table, not a
# geography service: unknown places simply fall back to word matching.
_PLACES: dict[str, tuple[str, str]] = {
    **{c: ("india", "in") for c in (
        "bangalore", "bengaluru", "hyderabad", "mumbai", "pune", "chennai",
        "kolkata", "noida", "gurgaon", "gurugram", "delhi", "ahmedabad",
        "jaipur", "kochi", "coimbatore", "indore", "chandigarh", "trivandrum",
    )},
    **{c: ("united states", "us") for c in (
        "york", "francisco", "seattle", "austin", "boston", "chicago",
        "denver", "atlanta", "angeles", "diego", "jose", "portland",
    )},
    **{c: ("united kingdom", "gb") for c in ("london", "manchester", "edinburgh", "cambridge")},
    **{c: ("germany", "de") for c in ("berlin", "munich", "münchen", "hamburg", "cologne", "frankfurt")},
    **{c: ("canada", "ca") for c in ("toronto", "vancouver", "montreal", "ottawa")},
    **{c: ("australia", "au") for c in ("sydney", "melbourne", "brisbane")},
    "singapore": ("singapore", "sg"),
    "dublin": ("ireland", "ie"),
    "amsterdam": ("netherlands", "nl"),
    "paris": ("france", "fr"),
    "warsaw": ("poland", "pl"),
    "krakow": ("poland", "pl"),
    "lisbon": ("portugal", "pt"),
    "madrid": ("spain", "es"),
    "barcelona": ("spain", "es"),
    "tokyo": ("japan", "jp"),
    "zurich": ("switzerland", "ch"),
    "tel": ("israel", "il"),
}

# Country names, so we can tell "India" (a country) from "Bengaluru" (a city).
_COUNTRIES: frozenset[str] = frozenset(
    {country for country, _ in _PLACES.values()}
) | {"usa", "us", "uk", "gb", "america", "britain", "england", "states", "kingdom"}


def country_of(place: str) -> str | None:
    """Country a place sits in, by city lookup or by naming a country outright."""
    toks = words(place)
    for tok in toks:
        if tok in _PLACES:
            return _PLACES[tok][0]
    for country in ("united states", "united kingdom"):
        if words(country) <= toks:
            return country
    for tok in toks:
        if tok in _COUNTRIES:
            return {
                "usa": "united states", "us": "united states",
                "america": "united states", "states": "united states",
                "uk": "united kingdom", "gb": "united kingdom",
                "britain": "united kingdom", "england": "united kingdom",
                "kingdom": "united kingdom",
            }.get(tok, tok)
    return None


def _names_country(place: str) -> bool:
    return bool(words(place) & _COUNTRIES)


def place_matches(wanted: str, text: str) -> bool:
    """Whether ``text`` names the place ``wanted``, matching whole words.

    Substring comparison is not safe for place names: "India" is a substring of
    "Remote - Indiana, USA", which is how an India-based search surfaced roles
    restricted to the American Midwest.

    A country also matches its own cities, so wanting "India" accepts a role in
    "Bengaluru". Two *cities* in one country never match each other — Hyderabad
    is not Bangalore — so at least one side has to name the country.
    """
    want, have = words(wanted), words(text)
    if not want or not have:
        return False
    if want <= have:
        return True
    # Single-token shorthands ("US" vs "United States").
    if len(want) == 1 and _PLACE_ALIASES.get(next(iter(want)), frozenset()) & have:
        return True

    wanted_country, text_country = country_of(wanted), country_of(text)
    if wanted_country and wanted_country == text_country:
        return _names_country(wanted) or _names_country(text)
    return False


# A word between a role's words that names another discipline: "Data
# Protection Engineer" is security work, not data engineering.
_OTHER_DISCIPLINE = frozenset({
    "protection", "security", "privacy", "center", "centre", "entry",
    "annotation", "annotator", "labeling", "labelling", "loss",
})


# A title whose job is plainly non-technical, though it names the field it
# serves: "Learning & Development Partner – Data Engineering Business".
_NON_TECH_ROLE = frozenset({
    "partner", "recruiter", "recruiting", "talent", "sales", "marketing",
    "trainer", "hrbp",
})


def _ordered_words(text: str) -> list[str]:
    return "".join(
        c if c.isalnum() or c in "+#" else " " for c in (text or "").lower()
    ).split()


def _reads_as_phrase(title: str, role: str) -> bool:
    """Whether a multi-word role reads as a phrase in the title.

    The role's first word must come before a word of its job family, at most
    one word apart, with nothing between them naming another discipline. So
    "Data Platform Engineer" fits "data engineer", but "Data Protection
    Engineer" and "AI Engineer — Data APIs" do not, though all contain both
    words.
    """
    role_seq = [w for w in _ordered_words(role) if w not in _LEVELS]
    family = _FAMILY.get(role_seq[-1]) if len(role_seq) >= 2 else None
    if family is None:
        return True
    first = expand_terms([role_seq[0]]) | {role_seq[0]}
    toks = _ordered_words(title)
    if family == "eng" and set(toks) & _NON_TECH_ROLE:
        return False
    for i, tok in enumerate(toks):
        if tok not in first:
            continue
        for j in range(i + 1, min(i + 3, len(toks))):
            if _FAMILY.get(toks[j]) == family and not (
                    set(toks[i + 1:j]) & _OTHER_DISCIPLINE):
                return True
    return False


def title_matches_role(title: str, role: str) -> bool:
    """Whether ``title`` plausibly names the job described by ``role``.

    Two conditions, both necessary:

    * every distinctive word of the role — everything except generic job-family
      nouns and level markers — appears in the title, allowing taxonomy synonyms
      so a "golang engineer" search matches a "Go Engineer" posting; and
    * the job families agree, so "data engineer" does not match "Data Analyst"
      on the strength of the word "data" alone.

    A role made only of generic words ("engineer") falls back to ordinary
    containment, since there is nothing distinctive to insist on.
    """
    role_words = words(role) - _LEVELS
    if not role_words:
        return True
    title_words = words(title)
    title_forms = title_words | {t.lower() for t in expand_terms(title_words)}

    distinctive = role_words - _GENERIC
    required = distinctive or role_words
    if not all(
        w in title_forms or bool(expand_terms([w]) & title_forms)
        for w in required
    ):
        return False

    role_families, title_families = _families(role_words), _families(title_words)
    if role_families and title_families and not role_families & title_families:
        return False
    return _reads_as_phrase(title, role)


def passes_local_filters(posting: JobPosting, query: dict) -> bool:
    role = (query.get("role") or "").strip()
    location = (query.get("location") or "").strip().lower()
    remote_ok = query.get("remote_ok", True)
    excluded = {c.lower() for c in query.get("exclude_companies", [])}
    candidate_level = query.get("candidate_level")

    if posting.company.lower() in excluded:
        return False

    if role and not title_matches_role(posting.title, role):
        return False

    if candidate_level is not None and not within_band(
        candidate_level, level_from_posting(posting)
    ):
        return False

    if location:
        places = [location, *(query.get("all_locations") or [])]
        if location in ANYWHERE:
            # "Remote" as a desired location means "I will take remote work",
            # not "any remote job on earth is open to me". A role restricted to
            # Argentina is still closed to a candidate in India.
            if posting.remote:
                return _remote_reaches(posting, places)
            return place_matches(location, posting.location)
        if place_matches(location, posting.location):
            return True
        # A remote posting is only a match for a specific city if the candidate
        # can actually take it from there. Treating every remote job as valid
        # for every location turned "Hyderabad" into "anywhere remote", which is
        # how a Berlin-only remote role surfaced for an India-based search.
        if remote_ok and posting.remote:
            return _remote_reaches(posting, places)
        return False

    return True


def _remote_reaches(posting: JobPosting, places: list[str]) -> bool:
    """Whether a remote posting is open to somebody in ``places``.

    Boards that publish a remote scope are taken at their word — see
    :func:`remote_scope`. Where no scope is published we allow the posting
    through: an unqualified "Remote" usually is, and dropping every unscoped
    remote job would empty the board.

    ``places`` is the candidate's whole location list rather than the single
    location being searched, because a scope of "India" has to satisfy a search
    for "Hyderabad" and plain string comparison cannot know a city sits inside a
    country. Matching against every location the candidate listed covers that
    without shipping a geography database. The residual gap is a candidate who
    lists only a city and never their country; they will miss country-scoped
    remote roles, which is why onboarding asks for several locations.
    """
    scope = remote_scope(posting)
    if not scope:
        return True
    if any(words(s) & ANYWHERE for s in scope):
        return True
    wanted = [p for p in places if p and p.strip() and p.strip().lower() not in ANYWHERE]
    if not wanted:
        # Candidate named no real place, only "Remote" — a scoped role is still
        # a guess, so let it through rather than hiding the whole board.
        return True
    return any(
        place_matches(w, s) or place_matches(s, w) for s in scope for w in wanted
    )


def remote_scope(posting: JobPosting) -> list[str]:
    """Places a remote posting says it is open to, or ``[]`` if it doesn't say.

    Two sources, in order. Boards that publish a structured scope (Himalayas'
    ``locationRestrictions``) are taken at their word. Greenhouse and Lever
    publish it *in the location string* instead — "Remote US", "Remote - India",
    "Remote Poland" — so the qualifier after "remote" is the scope. Missing that
    second form let every "Remote US" role surface for a candidate in India,
    which is most of a board like Affirm's.
    """
    raw = posting.raw or {}
    out: list[str] = []
    for key in ("remote_scope", "location_restrictions"):
        value = raw.get(key)
        if isinstance(value, str):
            out.extend(part.strip() for part in value.split(","))
        elif isinstance(value, (list, tuple)):
            out.extend(str(v).strip() for v in value)
    if out:
        return [s.lower() for s in out if s]

    text = (posting.location or "").lower()
    if not text:
        return []
    # "Remote - Illinois, USA; Remote - Ohio, USA" → one entry per listed place.
    parts = [
        p.replace("remote", " ").strip(" -–—,;")
        for p in text.replace(";", ",").split(",")
    ]
    # A bare "Remote" leaves nothing behind: genuinely unscoped.
    #
    # A concrete city *plus* a remote flag is not unscoped either — boards use
    # that to mean "remote, based near this office". Ashby marks some San
    # Francisco roles remote, and reading those as open-to-anywhere put US-only
    # jobs at the top of an India-based candidate's board.
    return [p for p in parts if p]
