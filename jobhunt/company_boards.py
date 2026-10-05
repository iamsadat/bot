"""A seeded set of public company job boards, so a new workspace finds real jobs.

Until now a brand-new workspace searched exactly two keyless sources: Arbeitnow
(a largely German aggregator) and Remote OK. For anyone outside Western Europe or
the US-remote market that is close to no supply at all — an India-based candidate
got a board of Berlin and London listings and concluded the product was broken.

Company boards fix that better than another aggregator would:

* **Signal.** These are first-party postings, freshly maintained, with full job
  descriptions — not scraped summaries.
* **Reach.** The boards below carry **3,108 India-located roles** between them —
  2,152 in Hyderabad or Bangalore, 95 with a data-engineering title — with no API
  key of any kind.
* **They are the boards we can actually apply to.** ``jobhunt.submitters`` has
  real submitters for Greenhouse and Lever only. Every posting from those two
  is a job the autonomy loop can genuinely submit, which is what turns
  "Connect an ATS to enable" from a dead end into a working default.

Every slug here was verified live against its public API on 2026-10-05, and the
counts are India-*located* roles — not "India or remote", which double-counts
every "Remote - Illinois, USA" listing. "Data-engineering title" means the title
names data engineering, analytics engineering, big data, ETL, a data platform,
Spark or Databricks. Boards that turned out to have no India presence at all are
deliberately absent: Affirm, Instacart, Ramp and Sierra, and on the 2026-10-05
re-check Brex, Plaid, Robinhood, Dataiku, Collibra and Intercom among others. So
are retired slugs — PhonePe's and Postman's Greenhouse boards both 404 as of
2026-10-05 — and job aggregators that re-post other companies' roles (Jobgether,
Weekday, Smart Working), whose postings are not first-party. See
``docs/MANUAL_TEST.md`` for how to re-check them.

Ordering matters, because boards are consumed a page at a time (see :func:`page`)
and each board is a full-board download. Each list runs best-first for a data
engineer in Hyderabad or Bangalore: boards with data-engineering roles open
there, then data roles elsewhere in India or India-remote, then India-heavy
boards with no data title open today, then a long tail of boards carrying a
handful of India roles. Within a tier, more data roles first, then more
Hyderabad/Bangalore roles — so the first sweep is the highest-yield one.
"""

from __future__ import annotations

import logging
import os

_log = logging.getLogger(__name__)

# Greenhouse board tokens, annotated (total postings / India-located /
# Hyderabad+Bangalore / data-engineering titles in India), measured 2026-10-05.
GREENHOUSE: tuple[str, ...] = (
    # Data-engineering roles open in Hyderabad or Bangalore right now.
    "capco",                            # 713 / 165 /  69 /  9
    "databricks",                       # 890 /  92 /  79 /  6
    "phdata",                           #  44 /  14 /  11 /  4
    "okta",                             # 366 / 111 / 109 /  2
    "coherehealth",                     #  75 /  42 /  42 /  2
    "sigmoid",                          #  36 /  31 /  31 /  2
    "zenoti",                           #  49 /  28 /  28 /  2
    "glance",                           #  38 /  22 /  22 /  2
    "accordionindia",                   #  21 /  21 /  21 /  2
    "hackerrank",                       #  28 /  18 /  18 /  2
    "arcanaanalytics",                  #  29 /  21 /  15 /  2
    "zetaglobal",                       # 141 /  41 /  41 /  1
    "celonis",                          # 226 /  32 /  32 /  1
    "point72",                          # 210 /  36 /  30 /  1
    "five9",                            #  99 /  31 /  21 /  1
    "smartsheet",                       #  91 /  21 /  21 /  1
    "doordashindia",                    #  19 /  19 /  16 /  1
    "netradyne",                        #  24 /  15 /  15 /  1
    "definitivehcindia",                #   5 /   5 /   5 /  1
    "prodigal",                         #  11 /  10 /   4 /  1
    "earnin",                           #  18 /   4 /   4 /  1
    "thoughtworks",                     #  38 /   4 /   3 /  1
    "knowbe4",                          #  65 /   2 /   2 /  1
    # Data-engineering roles elsewhere in India, or India-remote.
    "wpp",                              # 207 /  60 /   2 /  3
    "particle41llc",                    #  27 /  16 /   0 /  3
    "gatherai",                         #  10 /   4 /   0 /  2
    "inmobi",                           #  71 /  42 /  31 /  1
    "netskope",                         # 150 /  21 /  12 /  1
    "anaplan",                          # 144 /  28 /   1 /  1
    "equalexperts",                     #  16 /   5 /   1 /  1
    "pay2dc",                           #  10 /  10 /   0 /  1
    "dunnhumby",                        #  41 /   7 /   0 /  1
    "yipitdata",                        #  51 /   7 /   0 /  1
    "galaxydigitalservices",            #  36 /   6 /   0 /  1
    "techholding",                      #  15 /   3 /   0 /  1
    "formaaiinc3",                      #   2 /   2 /   0 /  1
    # India-heavy, though no data-engineering title is open today.
    "purestorage",                      # 362 /  83 /  82 /  0
    "zscaler",                          # 367 /  71 /  57 /  0
    "highradius",                       #  79 /  52 /  52 /  0
    "stripe",                           # 718 /  43 /  43 /  0
    "emergentlabsinc",                  #  42 /  34 /  34 /  0
    "rubrik",                           # 129 /  32 /  26 /  0
    "gitlab",                           # 209 /  28 /  26 /  0
    "openfx",                           #  41 /  27 /  26 /  0
    "arcesiumllc",                      #  49 /  25 /  25 /  0
    "gleanwork",                        # 128 /  25 /  24 /  0
    "globalhealthcareexchangeinc",      #  52 /  24 /  24 /  0
    "onetrust",                         #  92 /  22 /  22 /  0
    "fivetran",                         # 181 /  17 /  16 /  0
    "harnessinc",                       #  67 /  16 /  15 /  0
    "tide",                             #  85 /  29 /  14 /  0
    "razorpaysoftwareprivatelimited",   #  19 /  16 /  14 /  0
    "mongodb",                          # 387 /  80 /  13 /  0
    "toast",                            # 321 /  18 /  13 /  0
    "kraftonindia",                     #  13 /  13 /  13 /  0
    "neuraflash",                       #  75 /  13 /  13 /  0
    "commvault",                        #  56 /  14 /  12 /  0
    "redwoodsoftware",                  #  31 /  11 /  11 /  0
    "elastic",                          # 394 /  15 /  10 /  0
    "payoneer",                         # 104 /  33 /   8 /  0
    "chargepoint",                      #  35 /  15 /   7 /  0
    "66degrees",                        #  21 /  11 /   7 /  0
    "6sense",                           #  29 /  10 /   7 /  0
    "alphasense",                       # 199 /  31 /   6 /  0
    "pubmatic",                         #  76 /  34 /   2 /  0
    "wizinc",                           # 131 /  10 /   2 /  0
    "addepar1",                         # 104 /  16 /   1 /  0
    "druva",                            #  34 /  11 /   1 /  0
    "coinbase",                         # 229 /  11 /   1 /  0
    "agoda",                            # 297 /  12 /   0 /  0
    "towerresearchcapital",             #  91 /  11 /   0 /  0
    "twilio",                           # 126 /  10 /   0 /  0
    # Long tail: a handful of India roles each, often on a large global board.
    "newrelic",                         #  47 /   9 /   9 /  0
    "crunchyroll",                      #  82 /   9 /   9 /  0
    "datadog",                          # 440 /   8 /   7 /  0
    "couchbaseinc",                     #  18 /   9 /   6 /  0
    "samsara",                          # 245 /   6 /   6 /  0
    "adyen",                            # 220 /   6 /   5 /  0
    "dialpad",                          #  60 /   5 /   5 /  0
    "groww",                            #   7 /   7 /   4 /  0
    "cleoindia",                        #   4 /   4 /   4 /  0
    "observeai",                        #  12 /   4 /   4 /  0
    "envoyglobalinc",                   #  19 /   4 /   4 /  0
    "jfrog",                            #  63 /   4 /   4 /  0
    "mixpanel",                         #  63 /   4 /   4 /  0
    "zuora",                            #  25 /   5 /   3 /  0
    "airbnb",                           # 149 /   5 /   3 /  0
    "anthropic",                        # 637 /   5 /   3 /  0
    "meltplan",                         #   4 /   4 /   3 /  0
    "poppulo",                          #   9 /   3 /   3 /  0
    "sumologic",                        #   9 /   5 /   2 /  0
    "neo4j",                            #  66 /   5 /   2 /  0
    "turing",                           #  30 /   4 /   2 /  0
    "figma",                            # 162 /   2 /   2 /  0
    "singlestore",                      #  40 /   7 /   1 /  0
    "acryldata",                        #   7 /   1 /   1 /  0
    "cockroachlabs",                    #  19 /   1 /   1 /  0
    "convera",                          #  54 /   9 /   0 /  0
    "flexport",                         # 198 /   8 /   0 /  0
    "blinkhealth",                      #  69 /   7 /   0 /  0
    "taboola",                          #  81 /   7 /   0 /  0
    "fourkites",                        #  13 /   6 /   0 /  0
    "yugabyte",                         #  16 /   6 /   0 /  0
    "launchdarkly",                     #  59 /   6 /   0 /  0
    "theknotworldwide",                 #  24 /   5 /   0 /  0
    "starburst",                        #  30 /   5 /   0 /  0
    "hasbro",                           # 123 /   3 /   0 /  0
    "grafanalabs",                      # 120 /   2 /   0 /  0
    "fastly",                           #  43 /   1 /   0 /  0
    "scaleai",                          # 191 /   1 /   0 /  0
    "cloudflare",                       # 406 /   1 /   0 /  0
)

# Lever company slugs, annotated the same way. Lever slugs are case-sensitive.
LEVER: tuple[str, ...] = (
    # Data-engineering roles open in Hyderabad or Bangalore right now.
    "brillio-2",                        # 227 / 164 / 137 /  7
    "beghouconsulting",                 #  76 /  50 /  26 /  4
    "acceldata",                        #  38 /  14 /  14 /  3
    "ttecdigital",                      #  66 /  27 /  27 /  2
    "fampay",                           #  17 /  17 /  17 /  1
    # Data-engineering roles elsewhere in India, or India-remote.
    "valgenesis",                       #  16 /  14 /   4 /  3
    "mactores",                         #  33 /  30 /   0 /  3
    "shyftlabs",                        #  22 /   8 /   0 /  3
    # India-heavy, though no data-engineering title is open today.
    "meesho",                           #  59 /  59 /  53 /  0
    "hevodata",                         #  52 /  50 /  43 /  0
    "veeva",                            # 939 /  35 /  31 /  0
    "zeta",                             #  25 /  23 /  19 /  0
    "kobie",                            #  27 /  11 /  11 /  0
    "100ms",                            #  10 /  10 /  10 /  0
    "dnb",                              # 108 /  22 /   7 /  0
    "mindtickle",                       #  17 /  16 /   3 /  0
    "shieldai",                         # 596 /  11 /   2 /  0
    # Long tail: a handful of India roles each, often on a large global board.
    "cred",                             #   8 /   8 /   8 /  0
    "moonpay",                          #  29 /   7 /   6 /  0
    "sonatype",                         #  20 /   5 /   5 /  0
    "nium",                             #  17 /   9 /   4 /  0
    "coupa",                            #  34 /   4 /   3 /  0
    "matillion",                        #  18 /   3 /   3 /  0
    "epifi",                            #   2 /   2 /   2 /  0
    "pocketfm",                         #   8 /   2 /   2 /  0
    "findem",                           #   4 /   2 /   1 /  0
    "modeln",                           #  10 /   1 /   1 /  0
    "egen",                             #  13 /   1 /   1 /  0
    "GoToGroup",                        #  42 /   1 /   1 /  0
    "binance",                          # 316 /   1 /   1 /  0
)

# Ashby company slugs, annotated the same way. Discovery only — there is no
# Ashby submitter, so these postings are applied to on the company's own site.
ASHBY: tuple[str, ...] = (
    # Data-engineering roles open in Hyderabad or Bangalore right now.
    "tekion",                           # 101 /  78 /  68 /  1
    "sarvam",                           #  59 /  59 /  50 /  1
    # Data-engineering roles elsewhere in India, or India-remote.
    "junipersquare",                    #  39 /   6 /   2 /  1
    "paxos",                            #  14 /   1 /   0 /  1
    # India-heavy, though no data-engineering title is open today.
    "liveramp-inc",                     #  59 /  23 /  23 /  0
    "lumilens",                         # 157 /  20 /  20 /  0
    "netgear",                          #  46 /  28 /  14 /  0
    "meraki-labs",                      #  16 /  16 /  14 /  0
    "ema",                              #  41 /  15 /  13 /  0
    "plotlineso",                       #  15 /  12 /  12 /  0
    "spotdraft",                        #  15 /  11 /  11 /  0
    "span",                             #  33 /  10 /  10 /  0
    "certifyos",                        #  14 /  10 /   8 /  0
    "snowflake",                        # 347 /  12 /   7 /  0
    "openai",                           # 829 /  10 /   2 /  0
    # Long tail: a handful of India roles each, often on a large global board.
    "josys",                            #  23 /   7 /   7 /  0
    "harvey",                           # 324 /   7 /   7 /  0
    "bolna",                            #   7 /   7 /   6 /  0
    "aiprise",                          #  16 /   5 /   5 /  0
    "gainsight",                        #  17 /   4 /   4 /  0
    "notion",                           # 136 /   4 /   4 /  0
    "collinear-ai",                     #  15 /   3 /   3 /  0
    "anyscale",                         #  20 /   3 /   3 /  0
    "supa",                             #   3 /   2 /   2 /  0
    "astronomer",                       #  25 /   2 /   2 /  0
    "avoca",                            #  25 /   2 /   2 /  0
    "clickhouse",                       # 195 /   9 /   1 /  0
    "elevenlabs",                       # 165 /   6 /   0 /  0
    "atlan",                            #   6 /   4 /   0 /  0
    "circle",                           #  41 /   4 /   0 /  0
    "redis",                            #  32 /   3 /   0 /  0
    "amplitude",                        #  36 /   2 /   0 /  0
    "rearc",                            #   2 /   1 /   0 /  0
    "confluent",                        #  17 /   1 /   0 /  0
    "outmarket",                        #  19 /   1 /   0 /  0
    "oscilar",                          #  22 /   1 /   0 /  0
)

# Boards per sweep. Each is a full-board download of up to a few megabytes, so by
# default a sweep takes a slice rather than the whole list. Repeated "Fetch more"
# clicks advance through the slices, which is what makes the button return
# genuinely new companies instead of re-reading the same ones.
#
# ``JOBHUNT_BOARDS_PER_PAGE`` overrides it (see :func:`boards_per_page`). A single
# personal user can afford to read every board on every sweep: ``0`` or ``all``
# does exactly that — one page, never a next one.
BOARDS_PER_PAGE = 6

# The ``per_page`` that means "every board in one sweep".
ALL_BOARDS = 0


def boards_per_page() -> int:
    """Boards per sweep, from ``JOBHUNT_BOARDS_PER_PAGE``; :data:`ALL_BOARDS` = all.

    Read on every call rather than at import, so the setting can change without
    reloading the module. Unset means :data:`BOARDS_PER_PAGE`. Anything that is
    not a non-negative integer or ``all`` falls back to the default with a
    warning — a typo in an environment variable must not take discovery down.
    """
    raw = os.environ.get("JOBHUNT_BOARDS_PER_PAGE", "").strip().lower()
    if not raw:
        return BOARDS_PER_PAGE
    if raw == "all":
        return ALL_BOARDS
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 0:
        _log.warning(
            "ignoring JOBHUNT_BOARDS_PER_PAGE=%r (want a whole number or 'all'); "
            "reading %d boards per sweep", raw, BOARDS_PER_PAGE,
        )
        return BOARDS_PER_PAGE
    return value


def _lists() -> dict[str, tuple[str, ...]]:
    # Looked up at call time, not bound at import, so the lists stay patchable.
    return {"greenhouse": GREENHOUSE, "lever": LEVER, "ashby": ASHBY}


def _sizes(per_page: int | None) -> dict[str, int]:
    """Boards of each kind per sweep. Lever and Ashby get a third of the budget."""
    if per_page is None:
        per_page = boards_per_page()
    if per_page <= ALL_BOARDS:
        return {kind: max(1, len(items)) for kind, items in _lists().items()}
    side = max(1, per_page // 3)
    return {"greenhouse": per_page, "lever": side, "ashby": side}


def _pages(count: int, per_page: int) -> int:
    return max(1, -(-count // per_page))


def _slice(
    items: tuple[str, ...], page: int, per_page: int, cycle: int | None = None,
) -> list[str]:
    """The ``page``-th slice of ``items``, wrapping to the start when exhausted.

    Wrapping is by *page*, not by item offset. Taking ``offset % len(items)``
    put the cycle out of step — with 20 boards read 6 at a time, the fifth
    sweep started at board 4 rather than back at board 0, so some boards were
    read twice as often as others and the cycle never repeated cleanly.

    ``cycle`` is the length of one pass over *every* list (see
    :func:`total_pages`). A list that needs fewer pages than that starts over
    inside the cycle, but every list restarts together when the cycle does, so
    sweep ``cycle + 1`` reads exactly what sweep 1 read for all three kinds.
    """
    if not items:
        return []
    step = max(1, page) - 1
    if cycle:
        step %= cycle
    start = (step % _pages(len(items), per_page)) * per_page
    return list(items[start:start + per_page])


def _cycle(sizes: dict[str, int]) -> int:
    return max(_pages(len(items), sizes[kind]) for kind, items in _lists().items())


def page(number: int = 1, per_page: int | None = None) -> dict[str, list[str]]:
    """The boards to search on sweep ``number``, as ``{kind: [slugs]}``.

    ``per_page`` defaults to :func:`boards_per_page`; :data:`ALL_BOARDS` returns
    every seeded board. Wraps around when the list is exhausted, so continuous
    discovery keeps cycling instead of going quiet.
    """
    sizes = _sizes(per_page)
    cycle = _cycle(sizes)
    return {
        kind: _slice(items, number, sizes[kind], cycle)
        for kind, items in _lists().items()
    }


def total_pages(per_page: int | None = None) -> int:
    """How many sweeps it takes to read every seeded board at least once.

    Usually the Greenhouse list sets it, but at a small page size Lever and
    Ashby get a single board per sweep and can outlast it — counting only
    Greenhouse would report "no next page" before they had all been read.
    """
    return _cycle(_sizes(per_page))


def has_next(number: int, per_page: int | None = None) -> bool:
    """Whether another page of boards remains before wrapping."""
    return max(1, number) < total_pages(per_page)
