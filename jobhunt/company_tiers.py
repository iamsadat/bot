"""Company tier + recruiter search links, derived without any network call.

The tier is a curated, general-knowledge label for the companies on the seeded
boards (``jobhunt.company_boards``) and other well-known employers; a company
not listed is unrated rather than guessed. Contacts are LinkedIn people-search
deep links the user opens themselves — LinkedIn is never scraped (ToS, bans).
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlencode

_BY_TIER: dict[str, tuple[str, ...]] = {
    "Big Tech": (
        "Google", "Alphabet", "Meta", "Facebook", "Amazon", "AWS", "Apple",
        "Microsoft", "Netflix", "Nvidia", "Adobe", "Salesforce", "Oracle", "IBM",
        "Intel", "Cisco", "SAP", "Uber", "LinkedIn", "Airbnb",
    ),
    "Product unicorn": (
        "Databricks", "Stripe", "Anthropic", "OpenAI", "Figma", "Notion",
        "Scale AI", "Glean", "Gleanwork", "Harvey", "ElevenLabs", "Wiz", "Wizinc",
        "Fivetran", "Harness", "HarnessInc", "Celonis", "OneTrust", "AlphaSense",
        "Tekion", "Zenoti", "HighRadius", "Glance", "InMobi", "Netradyne",
        "Mindtickle", "Pocket FM", "Druva", "Anyscale", "Binance",
        # Indian product unicorns.
        "Swiggy", "Zomato", "Eternal", "Razorpay", "Razorpaysoftwareprivatelimited",
        "CRED", "Meesho", "PhonePe", "Flipkart", "Zepto", "Groww", "Zeta",
        "Postman", "BrowserStack", "Icertis", "Chargebee",
    ),
    "Mid-size product": (
        "Okta", "Zscaler", "Pure Storage", "MongoDB", "Elastic",
        "Datadog", "Twilio", "GitLab", "Toast", "Smartsheet", "Anaplan",
        "Netskope", "Commvault", "Five9", "New Relic", "Samsara",
        "Adyen", "Dialpad", "JFrog", "Mixpanel", "Zuora", "Neo4j",
        "Cockroach Labs", "Flexport", "Taboola", "LaunchDarkly",
        "Starburst", "Grafana Labs", "Fastly", "Cloudflare",
        "Snowflake", "Confluent", "ClickHouse", "Redis", "Amplitude", "Gainsight",
        "LiveRamp", "NETGEAR", "Veeva", "Coupa", "Dun & Bradstreet", "Sonatype",
        "Matillion", "MoonPay", "Coinbase", "Agoda", "Payoneer", "PubMatic",
        "ChargePoint", "Rubrik", "Arcesium", "ArcesiumLLC", "Zeta Global",
        "Crunchyroll", "Couchbase", "CouchbaseInc", "Yugabyte",
        "SingleStore", "HackerRank", "ValGenesis", "Freshworks", "Zoho",
        "DoorDash", "DoorDashIndia", "Circle", "Model N", "Nium",
    ),
    "Startup": (
        "Hevo Data", "Acceldata", "FamPay", "Sarvam AI", "SpotDraft",
        "Plotline", "100ms", "Bolna", "AiPrise", "Collinear AI", "Emergent Labs",
        "EmergentLabsInc", "Findem", "Prodigal", "Observe.AI",
        "Josys", "CertifyOS", "Fi", "Epifi", "Ema", "Cohere Health",
        "Astronomer", "Acryl Data", "Atlan",
    ),
    "Services/consulting": (
        "TCS", "Tata Consultancy Services", "Infosys", "Wipro", "Accenture",
        "Capgemini", "Cognizant", "Capco", "LTIMindtree", "HCL", "HCLTech",
        "Tech Mahindra", "Deloitte", "EY", "KPMG", "PwC", "McKinsey",
        "Thoughtworks", "WPP", "Brillio", "Beghou Consulting", "TTEC Digital",
        "Accordion", "AccordionIndia", "phData", "Particle41", "Particle41LLC",
        "Equal Experts", "66degrees", "NeuraFlash", "Mactores",
        "Egen", "Rearc", "Tech Holding", "Sigmoid", "dunnhumby",
    ),
}

# Legal/locale suffixes that vary between how a board and a posting name a company.
_NOISE = {"inc", "llc", "ltd", "limited", "pvt", "private", "india", "technologies",
          "technology", "software", "corp", "corporation", "co", "company", "the", "plc", "gmbh"}


def _norm(company: str) -> str:
    words = re.findall(r"[a-z0-9]+", (company or "").lower().replace("&", " and "))
    return "".join(w for w in words if w not in _NOISE)


_TIERS = {_norm(name): tier for tier, names in _BY_TIER.items() for name in names}


def tier_for(company: str) -> str:
    """The company's tier, or "" when it isn't in the curated map."""
    return _TIERS.get(_norm(company), "")


_INDIA_GEO = '["102713980"]'


def linkedin_links(company: str) -> list[dict]:
    """LinkedIn people-search links for recruiters and data-engineering managers
    at the company, filtered to India."""
    company = (company or "").strip()
    if not company:
        return []

    def url(keywords: str) -> str:
        qs = urlencode({"keywords": keywords, "geoUrn": _INDIA_GEO}, quote_via=quote)
        return f"https://www.linkedin.com/search/results/people/?{qs}"

    return [
        {"label": f"Recruiters at {company}",
         "url": url(f"{company} recruiter OR talent acquisition")},
        {"label": f"Data engineering managers at {company}",
         "url": url(f"{company} data engineering manager")},
    ]
