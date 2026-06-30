"""Skill-gap learning paths — turn missing ATS keywords into a study plan.

Aggregates ``missing_keywords`` across every tailored document in the
dashboard state, ranks the gaps by how often they show up, and attaches a
small set of curated learning resources per skill (falling back to a generic
search link for anything not in the curated map).
"""

from __future__ import annotations

from jobhunt.skills_taxonomy import expand_term

# Curated resources for common skills. Keys are canonical lowercase skill
# names; values are lists of {"title", "url"} dicts. Deliberately small and
# high-signal rather than exhaustive — anything missing falls back to
# ``_generic_resources``.
_RESOURCES: dict[str, list[dict[str, str]]] = {
    "kubernetes": [
        {"title": "Kubernetes Documentation", "url": "https://kubernetes.io/docs/home/"},
        {"title": "Kubernetes Basics Tutorial",
         "url": "https://kubernetes.io/docs/tutorials/kubernetes-basics/"},
    ],
    "docker": [
        {"title": "Docker Get Started Guide", "url": "https://docs.docker.com/get-started/"},
        {"title": "Docker Curriculum", "url": "https://docker-curriculum.com/"},
    ],
    "python": [
        {"title": "Official Python Tutorial", "url": "https://docs.python.org/3/tutorial/"},
        {"title": "Real Python", "url": "https://realpython.com/"},
    ],
    "react": [
        {"title": "React Documentation", "url": "https://react.dev/learn"},
        {"title": "Epic React", "url": "https://www.epicreact.dev/"},
    ],
    "aws": [
        {"title": "AWS Skill Builder", "url": "https://skillbuilder.aws/"},
        {"title": "AWS Well-Architected Framework",
         "url": "https://aws.amazon.com/architecture/well-architected/"},
    ],
    "system design": [
        {"title": "System Design Primer",
         "url": "https://github.com/donnemartin/system-design-primer"},
        {"title": "Designing Data-Intensive Applications (book)",
         "url": "https://dataintensive.net/"},
    ],
    "sql": [
        {"title": "Mode SQL Tutorial", "url": "https://mode.com/sql-tutorial/"},
        {"title": "PostgreSQL Tutorial", "url": "https://www.postgresqltutorial.com/"},
    ],
    "javascript": [
        {"title": "MDN JavaScript Guide",
         "url": "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide"},
        {"title": "JavaScript.info", "url": "https://javascript.info/"},
    ],
    "typescript": [
        {"title": "TypeScript Handbook",
         "url": "https://www.typescriptlang.org/docs/handbook/intro.html"},
    ],
    "golang": [
        {"title": "A Tour of Go", "url": "https://go.dev/tour/welcome/1"},
        {"title": "Effective Go", "url": "https://go.dev/doc/effective_go"},
    ],
    "machine-learning": [
        {"title": "Google's Machine Learning Crash Course",
         "url": "https://developers.google.com/machine-learning/crash-course"},
        {"title": "fast.ai Practical Deep Learning", "url": "https://course.fast.ai/"},
    ],
    "terraform": [
        {"title": "Terraform Documentation",
         "url": "https://developer.hashicorp.com/terraform/docs"},
    ],
    "kafka": [
        {"title": "Apache Kafka Documentation", "url": "https://kafka.apache.org/documentation/"},
    ],
    "graphql": [
        {"title": "GraphQL Official Docs", "url": "https://graphql.org/learn/"},
    ],
    "redis": [
        {"title": "Redis Documentation", "url": "https://redis.io/docs/latest/"},
    ],
    "postgres": [
        {"title": "PostgreSQL Tutorial", "url": "https://www.postgresqltutorial.com/"},
    ],
    "distributed-systems": [
        {"title": "MIT 6.824 Distributed Systems",
         "url": "https://pdos.csail.mit.edu/6.824/"},
    ],
    "microservices": [
        {"title": "microservices.io", "url": "https://microservices.io/"},
    ],
}


def _generic_resources(skill: str) -> list[dict[str, str]]:
    query = skill.replace(" ", "+")
    return [{
        "title": f"Search: {skill}",
        "url": f"https://www.google.com/search?q=learn+{query}",
    }]


def _canonical_skill(term: str) -> str:
    """Pick a stable canonical name for a synonym group: the shortest member
    that is also a key in ``_RESOURCES``, else the shortest member overall."""
    group = sorted(expand_term(term))
    if not group:
        return term
    for candidate in group:
        if candidate in _RESOURCES:
            return candidate
    return min(group, key=len)


def resources_for(skill: str) -> list[dict[str, str]]:
    """Curated resources for a skill, falling back to a generic search link."""
    for candidate in sorted(expand_term(skill)):
        if candidate in _RESOURCES:
            return _RESOURCES[candidate]
    return _generic_resources(skill)


def compute_skill_gaps(state, *, top: int = 10) -> list[dict]:
    """Aggregate ``missing_keywords`` across all tailored documents.

    Synonym variants (e.g. "k8s" / "kubernetes") are merged into a single
    canonical gap via ``expand_term`` before ranking, so frequency reflects
    the underlying skill rather than its surface form. Returns a list of
    ``{"skill", "count", "resources"}`` dicts ranked by descending count
    (ties broken alphabetically for stable output), capped at ``top``.
    """
    counts: dict[str, int] = {}
    for doc in state.documents.values():
        for kw in doc.get("missing_keywords", []) or []:
            kw = str(kw).strip().lower()
            if not kw:
                continue
            canonical = _canonical_skill(kw)
            counts[canonical] = counts.get(canonical, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return [
        {"skill": skill, "count": count, "resources": resources_for(skill)}
        for skill, count in ranked
    ]
