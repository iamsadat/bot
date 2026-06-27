"""Production-grade JD parser with HTML stripping and TF-IDF keyword extraction.

Phase 2 upgrade over the basic keyword extractor in resume.py. Combines:

* HTML stripping (handles boards-api Greenhouse / Lever / Ashby payloads).
* TF-IDF scoring against an internal corpus of common job descriptions —
  lifts genuinely-distinctive terms (kubernetes, redis, langgraph) above
  noise words ("strong communicator", "team player").
* ATS keyword categorisation (skills / qualifications / responsibilities).
* Cross-check between TF-IDF ranking and frequency to surface terms that
  appear in either signal, then take the union for ATS-friendly coverage.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass
class ParsedJD:
    """Structured representation of a job description."""

    raw: str
    cleaned: str
    keywords_tfidf: list[tuple[str, float]]  # (term, score)
    keywords_freq: list[tuple[str, int]]
    union_keywords: list[str]  # combined ranking
    skills: list[str]  # likely-skill nouns
    qualifications: list[str]  # "X years", "BS in CS"
    responsibilities: list[str]  # action verbs
    sections: dict[str, str] = field(default_factory=dict)


# ----------------------------------------------------------- HTML stripping

class _HTMLToText(HTMLParser):
    """Strip tags, preserve whitespace at boundary."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0  # depth-counted skip for script/style

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("p", "div", "li", "br", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Convert HTML to plain text, preserving paragraph boundaries."""
    if "<" not in html:
        return html
    p = _HTMLToText()
    p.feed(html)
    text = "".join(p.parts)
    # Collapse repeated whitespace; keep paragraph breaks.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


# -------------------------------------------------------- token + stopwords

_STOP = frozenset({
    "the", "and", "for", "with", "you", "are", "our", "this", "that",
    "have", "has", "from", "will", "your", "should", "must", "into",
    "using", "use", "be", "to", "in", "of", "a", "an", "on", "or", "is",
    "as", "we", "us", "by", "at", "it", "experience", "knowledge",
    "familiarity", "team", "work", "role", "candidate", "position",
    "company", "ability", "skills", "responsibilities", "qualifications",
    "requirements", "preferred", "required", "minimum", "plus", "years",
    "year", "etc", "such", "including", "across", "while", "where", "what",
    "who", "how", "when", "why", "all", "any", "some", "many", "more",
    "other", "than", "but", "not", "no", "yes", "if", "so", "do", "does",
    "did", "had", "was", "were", "been", "being", "their", "they", "them",
    "his", "her", "its", "him", "she", "he", "i", "me", "my", "mine",
    "ours", "yours", "self", "join", "looking", "seeking", "growth",
    "great", "good", "best", "new", "able", "make", "via",
})

# Allow alphanumerics + +, -, # for techy names (c++, c#, etc).
_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z+\-#0-9]{2,}")

# Reference corpus — used for IDF computation. Real prod: load from DB.
_REFERENCE_CORPUS: list[str] = [
    "backend engineer python distributed systems postgres redis kubernetes",
    "frontend engineer typescript react webpack design",
    "data scientist python pandas pytorch ml deep learning",
    "product manager roadmap stakeholders agile customer",
    "marketing campaigns growth analytics seo brand",
    "sales pipeline crm quota outbound prospecting",
    "devops kubernetes terraform aws ci cd deployment",
    "security pentest vulnerability compliance audit risk",
    "designer figma user interaction prototyping research",
    "qa testing automation playwright selenium coverage",
]


def _tokenise(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP]


# ------------------------------------------------------------------ TF-IDF

def _compute_idf(corpus: list[str]) -> dict[str, float]:
    """Inverse document frequency over a reference corpus."""
    n_docs = len(corpus)
    df: dict[str, int] = Counter()
    for doc in corpus:
        for tok in set(_tokenise(doc)):
            df[tok] += 1
    return {t: math.log((n_docs + 1) / (c + 1)) + 1.0 for t, c in df.items()}


# Cached once per process — reference corpus is static.
_IDF = _compute_idf(_REFERENCE_CORPUS)


def tfidf_keywords(text: str, limit: int = 25) -> list[tuple[str, float]]:
    """Rank tokens by TF-IDF (high score = distinctive to this JD)."""
    tokens = _tokenise(text)
    if not tokens:
        return []
    tf = Counter(tokens)
    total = sum(tf.values())
    # Unknown terms (not in corpus) get max IDF — treats them as distinctive.
    max_idf = max(_IDF.values()) if _IDF else 1.0
    scores = {
        term: (count / total) * _IDF.get(term, max_idf)
        for term, count in tf.items()
    }
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


def frequency_keywords(text: str, limit: int = 25) -> list[tuple[str, int]]:
    """Plain frequency ranking — catches things TF-IDF undervalues."""
    counts = Counter(_tokenise(text))
    return counts.most_common(limit)


# ----------------------------------------------------------- categorisation

# Indicative cues for each ATS section type. Production: maintain in DB.
_QUALIFICATION_PATTERNS = [
    re.compile(r"\b\d+\+?\s*years?\b", re.I),
    re.compile(r"\bb[as]c?\.?\s*in\b", re.I),
    re.compile(r"\bm[as]c?\.?\s*in\b", re.I),
    re.compile(r"\bph\.?d\b", re.I),
    re.compile(r"\bdegree\b", re.I),
]
_RESPONSIBILITY_VERBS = frozenset({
    "build", "develop", "design", "architect", "lead", "drive", "ship",
    "implement", "deliver", "collaborate", "mentor", "scale", "optimize",
    "monitor", "debug", "test", "deploy", "maintain", "review",
})
# Lightweight skill heuristic — anything in a known tech taxonomy.
_KNOWN_TECH = frozenset({
    "python", "javascript", "typescript", "go", "golang", "rust", "java",
    "c++", "c#", "ruby", "php", "swift", "kotlin", "scala",
    "react", "vue", "angular", "svelte", "node", "fastapi", "django",
    "flask", "rails", "spring", "express", "nextjs", "nuxt",
    "postgres", "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
    "dynamodb", "cassandra", "kafka", "rabbitmq",
    "aws", "gcp", "azure", "kubernetes", "k8s", "docker", "terraform",
    "ansible", "helm", "linux", "bash",
    "pytorch", "tensorflow", "pandas", "numpy", "scikit", "transformers",
    "langchain", "langgraph",
    "graphql", "rest", "grpc", "websocket",
    "git", "github", "gitlab", "jenkins", "circleci",
    "opentelemetry", "prometheus", "grafana", "datadog",
    "playwright", "selenium", "jest", "pytest", "vitest",
    "pgvector", "embeddings",
})


def categorise(text: str, keywords: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Split keywords into skills / quals / responsibilities."""
    text_l = text.lower()
    skills = [k for k in keywords if k in _KNOWN_TECH]
    quals: list[str] = []
    for pat in _QUALIFICATION_PATTERNS:
        for m in pat.finditer(text_l):
            phrase = m.group(0).strip().lower()
            if phrase not in quals:
                quals.append(phrase)
    resps = [k for k in keywords if k in _RESPONSIBILITY_VERBS]
    return skills, quals, resps


# -------------------------------------------------------------- sectioning

_SECTION_HEADERS = {
    "responsibilities": ["responsibilities", "what you'll do", "the role"],
    "requirements": ["requirements", "qualifications", "what you'll bring",
                     "you have", "must have"],
    "nice_to_have": ["nice to have", "preferred", "bonus", "plus"],
    "about": ["about us", "about the company", "who we are"],
}


def split_sections(text: str) -> dict[str, str]:
    """Crude section splitter — header line + body until next header."""
    lines = text.split("\n")
    sections: dict[str, list[str]] = {}
    current_key: str | None = None
    for line in lines:
        stripped = line.strip().lower().rstrip(":")
        matched_key: str | None = None
        for key, headers in _SECTION_HEADERS.items():
            if any(stripped == h or stripped.startswith(h) for h in headers):
                matched_key = key
                break
        if matched_key:
            current_key = matched_key
            sections.setdefault(current_key, [])
            continue
        if current_key:
            sections[current_key].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items() if v}


# --------------------------------------------------------------- top-level

def parse_jd(html_or_text: str, limit: int = 20) -> ParsedJD:
    """One-shot parse of a JD blob.

    Args:
        html_or_text: Raw JD text or HTML (Greenhouse/Lever/Ashby output).
        limit: Max keywords to surface (both TF-IDF and frequency).

    Returns:
        ParsedJD with cleaned text, ranked keywords, categorised skills.
    """
    cleaned = html_to_text(html_or_text)
    kw_tfidf = tfidf_keywords(cleaned, limit=limit)
    kw_freq = frequency_keywords(cleaned, limit=limit)

    # Union of both rankings — TF-IDF and frequency catch different signals.
    seen: set[str] = set()
    union: list[str] = []
    for term, _ in kw_tfidf:
        if term not in seen:
            seen.add(term)
            union.append(term)
    for term, _ in kw_freq:
        if term not in seen:
            seen.add(term)
            union.append(term)

    skills, quals, resps = categorise(cleaned, union)
    sections = split_sections(cleaned)

    return ParsedJD(
        raw=html_or_text,
        cleaned=cleaned,
        keywords_tfidf=kw_tfidf,
        keywords_freq=kw_freq,
        union_keywords=union[:limit],
        skills=skills,
        qualifications=quals,
        responsibilities=resps,
        sections=sections,
    )
