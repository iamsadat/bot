"""Skill synonym / alias taxonomy for relevance + ATS-coverage matching.

Exact string matching misses obvious equivalences ("k8s" vs "kubernetes",
"py" vs "python", "go" vs "golang"). This module groups related terms so
discovery relevance scoring and résumé keyword matching can treat them as the
same skill — raising both how relevant discovered jobs look and the ATS
keyword coverage of tailored résumés.

Groups are kept tight (true synonyms, or a skill plus its dominant ecosystem)
so expansion sharpens matching without diluting it. Tokens are normalised to
lowercase; the discovery/résumé tokenizers keep ``+ - #`` so terms like
``c++``, ``k8s``, ``ci-cd`` survive tokenisation.
"""

from __future__ import annotations

from collections.abc import Iterable

# Each set is a group of interchangeable / closely-related terms.
_GROUPS: list[set[str]] = [
    {"python", "py", "django", "flask", "fastapi"},
    {"javascript", "js", "ecmascript"},
    {"typescript", "ts"},
    {"node", "nodejs"},
    {"react", "reactjs"},
    {"nextjs"},
    {"vue", "vuejs"},
    {"angular"},
    {"kubernetes", "k8s"},
    {"docker", "containers", "containerd"},
    {"postgres", "postgresql", "psql"},
    {"mysql", "mariadb"},
    {"mongodb", "mongo"},
    {"rest", "restful"},
    {"graphql", "gql"},
    {"aws", "ec2", "s3", "lambda", "dynamodb"},
    {"gcp", "bigquery"},
    {"ci-cd", "cicd"},
    {"machine-learning", "ml", "pytorch", "tensorflow", "scikit-learn", "sklearn"},
    {"nlp"},
    {"golang", "go"},
    {"c++", "cpp"},
    {"c#", "csharp", "dotnet"},
    {"kafka"},
    {"redis", "memcached"},
    {"terraform", "iac"},
    {"microservices"},
    {"distributed-systems", "distributed"},
]

# term -> the union of all terms in any group containing it
_INDEX: dict[str, set[str]] = {}
for _g in _GROUPS:
    for _t in _g:
        _INDEX.setdefault(_t, set()).update(_g)


def _norm(term: str) -> str:
    return term.strip().lower()


def expand_term(term: str) -> set[str]:
    """Return ``term`` plus any known synonyms/aliases (all lowercased)."""
    t = _norm(term)
    return set(_INDEX.get(t, {t}))


def expand_terms(terms: Iterable[str]) -> set[str]:
    """Union-expand a collection of terms with their synonyms."""
    out: set[str] = set()
    for term in terms:
        out |= expand_term(term)
    return out
