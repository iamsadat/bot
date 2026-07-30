"""A seeded set of public company job boards, so a new workspace finds real jobs.

Until now a brand-new workspace searched exactly two keyless sources: Arbeitnow
(a largely German aggregator) and Remote OK. For anyone outside Western Europe or
the US-remote market that is close to no supply at all — an India-based candidate
got a board of Berlin and London listings and concluded the product was broken.

Company boards fix that better than another aggregator would:

* **Signal.** These are first-party postings, freshly maintained, with full job
  descriptions — not scraped summaries.
* **Reach.** The boards below carry **681 India-located roles** between them, with
  no API key of any kind.
* **They are the boards we can actually apply to.** ``jobhunt.submitters`` has
  real submitters for Greenhouse and Lever only. Every posting from those two
  is a job the autonomy loop can genuinely submit, which is what turns
  "Connect an ATS to enable" from a dead end into a working default.

Every slug here was verified live against its public API, and the counts are
India-*located* roles — not "India or remote", which double-counts every
"Remote - Illinois, USA" listing. Boards that turned out to have no India
presence at all (Affirm, Instacart, Ramp, Sierra) are deliberately absent, as are
retired slugs. See ``docs/MANUAL_TEST.md`` for how to re-check them.

Ordering matters, because boards are consumed a page at a time (see :func:`page`)
and each board is a full-board download. India-native companies come first: their
boards are small and almost entirely India-based, so the first sweep is both the
cheapest and the highest-yield. The large multinationals follow — hundreds of
postings each for a few dozen India roles.
"""

from __future__ import annotations

# Greenhouse board tokens, annotated (total postings / India-located).
GREENHOUSE: tuple[str, ...] = (
    # India-native or India-heavy: small boards, high hit rate.
    "phonepe",                          #  68 / 47
    "razorpaysoftwareprivatelimited",   #  28 / 22
    "hackerrank",                       #  33 / 21
    "zenoti",                           #  36 / 18
    "druva",                            #  27 / 10
    "groww",                            #  10 / 10
    # Global companies with real India engineering presence.
    "databricks",                       # 804 / 77
    "zscaler",                          # 305 / 74
    "purestorage",                      # 308 / 66
    "mongodb",                          # 396 / 55
    "stripe",                           # 539 / 39
    "gitlab",                           # 183 / 31
    "twilio",                           # 187 / 24
    "rubrik",                           # 108 / 24
    "netskope",                         # 130 / 18
    "elastic",                          # 224 / 13
    "postman",                          # 107 / 11
    "datadog",                          # 426 / 10
    "airbnb",                           # 188 /  9
    "fastly",                           #  54 /  8
)

# Lever company slugs.
LEVER: tuple[str, ...] = (
    "meesho",                           #  49 / 45
    "fampay",                           #  14 / 14
    "cred",                             #   3 /  3
)

# Ashby company slugs. Discovery only — there is no Ashby submitter, so these
# postings are applied to on the company's own site.
ASHBY: tuple[str, ...] = (
    "harvey",                           # 348 / 12
    "openai",                           # 748 /  9
)

# Boards per sweep. Each is a full-board download of up to a few megabytes, so a
# sweep takes a slice rather than the whole list. Repeated "Fetch more" clicks
# advance through the slices, which is what makes the button return genuinely new
# companies instead of re-reading the same ones.
BOARDS_PER_PAGE = 6


def _slice(items: tuple[str, ...], page: int, per_page: int) -> list[str]:
    if not items:
        return []
    start = ((max(1, page) - 1) * per_page) % len(items)
    return list(items[start:start + per_page])


def page(number: int = 1, per_page: int = BOARDS_PER_PAGE) -> dict[str, list[str]]:
    """The boards to search on sweep ``number``, as ``{kind: [slugs]}``.

    Wraps around when the list is exhausted, so continuous discovery keeps
    cycling instead of going quiet.
    """
    return {
        "greenhouse": _slice(GREENHOUSE, number, per_page),
        "lever": _slice(LEVER, number, max(1, per_page // 3)),
        "ashby": _slice(ASHBY, number, max(1, per_page // 3)),
    }


def total_pages(per_page: int = BOARDS_PER_PAGE) -> int:
    """How many sweeps it takes to walk the whole Greenhouse list once."""
    return max(1, -(-len(GREENHOUSE) // per_page))


def has_next(number: int, per_page: int = BOARDS_PER_PAGE) -> bool:
    """Whether another page of boards remains before wrapping."""
    return max(1, number) < total_pages(per_page)
