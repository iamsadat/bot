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

# Each list is a group of interchangeable / closely-related terms. The **first**
# entry is the group's canonical name — the one shown to users — so keep the
# recognisable spelling first ("python", not "py").
_GROUPS: list[list[str]] = [
    ["python", "py", "django", "flask", "fastapi"],
    ["javascript", "js", "ecmascript"],
    ["typescript", "ts"],
    ["node", "nodejs"],
    ["react", "reactjs"],
    ["nextjs"],
    ["vue", "vuejs"],
    ["angular"],
    ["kubernetes", "k8s"],
    ["docker", "containers", "containerd"],
    ["postgres", "postgresql", "psql"],
    ["mysql", "mariadb"],
    ["mongodb", "mongo"],
    ["rest", "restful"],
    ["graphql", "gql"],
    ["aws", "ec2", "s3", "lambda", "dynamodb"],
    ["gcp", "google-cloud"],
    # Cloud data services are distinct requirements, not aliases of their
    # cloud: "BigQuery" in a JD is a skill to show, not a synonym for GCP.
    ["bigquery"],
    ["dataproc"],
    ["dataflow"],
    ["cloud-composer"],
    ["aws-glue", "glue"],
    ["emr"],
    ["athena"],
    ["azure-data-factory", "adf", "data-factory"],
    ["synapse", "azure-synapse"],
    ["ci-cd", "cicd"],
    ["machine-learning", "ml", "pytorch", "tensorflow", "scikit-learn", "sklearn"],
    ["nlp"],
    ["golang", "go"],
    ["c++", "cpp"],
    ["c#", "csharp", "dotnet"],
    ["kafka"],
    ["redis", "memcached"],
    ["terraform", "iac"],
    ["microservices"],
    ["distributed-systems"],  # bare "distributed" matched "distributed teams"
    # Data engineering. Absent until now, which meant a PySpark/Databricks
    # résumé matched none of a data-engineering JD's actual requirements.
    ["spark", "pyspark", "apache-spark", "spark-sql"],
    ["sql", "t-sql", "tsql", "plsql", "pl-sql"],
    ["nosql"],
    ["lakehouse", "delta-lake", "iceberg", "hudi"],
    ["unity-catalog"],
    ["fivetran"],
    ["airbyte"],
    ["databricks"],
    ["etl", "elt", "data-pipelines", "pipelines"],
    ["airflow", "apache-airflow", "orchestration"],
    ["dbt"],
    ["snowflake"],
    ["redshift"],
    ["hadoop", "hive", "mapreduce"],
    ["sas"],
    ["data-warehouse", "warehousing", "datawarehouse"],
    ["data-modeling", "data-modelling", "dimensional-modeling", "star-schema"],
    ["prisma"],
    ["planetscale"],
    ["pandas", "numpy"],
    ["tableau", "powerbi", "power-bi", "looker"],
    ["scala"],
    ["rust"],
    ["kotlin"],
    ["swift"],
    ["php", "laravel"],
    ["ruby", "rails"],
    ["java", "spring", "springboot"],
    ["elasticsearch", "opensearch"],
    ["azure"],
    ["linux", "unix", "bash", "shell"],
    ["git"],
    ["grpc", "protobuf"],
    ["websocket", "websockets"],
    ["webrtc"],
    ["crdt", "crdts"],
    ["tailwind", "tailwindcss"],
    ["selenium", "playwright", "cypress"],
    ["pytest", "jest", "junit"],
]

# term -> the union of all terms in any group containing it
_INDEX: dict[str, set[str]] = {}
# term -> the canonical (first-listed) name of its group
_PRIMARY: dict[str, str] = {}
for _g in _GROUPS:
    for _t in _g:
        _INDEX.setdefault(_t, set()).update(_g)
        _PRIMARY.setdefault(_t, _g[0])


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


# --------------------------------------------------------------------------- #
# Finding the skills a document names
# --------------------------------------------------------------------------- #

# Terms worth recognising that aren't part of any synonym group. Everything in
# _GROUPS is already known; this is the long tail of standalone technologies.
_STANDALONE: frozenset[str] = frozenset({
    "html", "css", "sass", "scss", "svelte", "nuxt", "vite", "webpack",
    "express", "nestjs", "graphql", "apollo", "trpc", "zod",
    "sqlite", "cassandra", "dynamodb", "neo4j", "clickhouse", "duckdb",
    "rabbitmq", "celery", "sqs", "pubsub", "kinesis", "flink", "beam",
    "helm", "ansible", "puppet", "chef", "vagrant", "nginx",
    "jenkins", "circleci", "travis", "argocd", "gitops", "github-actions",
    "prometheus", "grafana", "datadog", "sentry", "opentelemetry", "splunk",
    "keras", "huggingface", "transformers", "langchain", "llm", "rag",
    "openai", "pgvector", "embeddings", "mlops", "feature-store",
    "figma", "jira", "confluence", "notion", "agile", "scrum", "kanban",
    "oauth", "jwt", "saml", "sso", "rbac", "encryption",
    "stripe", "twilio", "sendgrid", "firebase", "supabase", "vercel",
    "android", "ios", "flutter", "react-native",
    "matlab", "r", "julia", "perl", "cobol", "fortran", "abap",
    "excel", "vba", "sql-server", "oracle", "db2", "teradata", "informatica",
    "talend", "ssis", "pyspark-sql", "parquet", "avro",
    "great-expectations", "dagster", "prefect", "luigi", "sqoop", "nifi",
})

KNOWN_SKILLS: frozenset[str] = frozenset(_INDEX) | _STANDALONE

# Skill names that are also ordinary English words. Lowercase scanning would
# match "we go fast" as Golang and "or r" as the R language, which then inflates
# skill coverage on job descriptions that name no technology at all. As language
# names these are always capitalised, so scan them case-sensitively.
_AMBIGUOUS: dict[str, str] = {
    "go": r"\bGo\b", "r": r"\bR\b", "c": r"\bC\b", "d": r"\bD\b",
    "rest": r"\bREST\b", "ml": r"\bML\b", "ts": r"\bTS\b", "js": r"\bJS\b",
    "py": r"\bPy\b", "sas": r"\bSAS\b", "iac": r"\bIaC\b",
    "excel": r"\bExcel\b", "beam": r"\bBeam\b", "chef": r"\bChef\b",
    "hive": r"\bHive\b", "glue": r"\bGlue\b", "adf": r"\bADF\b",
    "spring": r"\bSpring\b",
}

# Multi-word / hyphenated terms need substring matching rather than token
# matching, because "machine learning" tokenises into two ordinary words.
_COMPOUND: tuple[str, ...] = tuple(
    sorted((t for t in KNOWN_SKILLS if "-" in t), key=len, reverse=True)
)

_WORD_RE = None  # lazily compiled below to keep import order simple


def _tokens(text: str) -> set[str]:
    global _WORD_RE
    if _WORD_RE is None:
        import re

        _WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z+#0-9]*")
    return {t.lower() for t in _WORD_RE.findall(text or "")}


def canonical(term: str) -> str:
    """The display name for a term's synonym group.

    Lets a caller count "k8s" and "kubernetes" in the same document as one
    skill, and show the reader "kubernetes" rather than whichever alias
    happened to appear.
    """
    return _PRIMARY.get(_norm(term), _norm(term))


def skills_in_text(text: str) -> set[str]:
    """Canonical skills named in ``text``.

    Vocabulary lookup rather than frequency ranking. Ranking a job description
    by term frequency surfaces its prose — "benefits", "culture", "ownership",
    "training" — because that is genuinely what a JD repeats most. Asking
    instead "which technologies from a known vocabulary does this document
    mention" gives the requirements a candidate can actually be matched against.
    """
    if not text:
        return set()
    import re

    tokens = _tokens(text)
    found = {t for t in KNOWN_SKILLS if t in tokens and t not in _AMBIGUOUS}

    # Terms that double as English words only count when written the way the
    # technology is written.
    for term, pattern in _AMBIGUOUS.items():
        if term in KNOWN_SKILLS and re.search(pattern, text):
            found.add(term)

    # Compound terms: normalise separators so "machine learning",
    # "machine-learning" and "machine  learning" all hit.
    flat = " ".join(text.lower().replace("-", " ").replace("/", " ").split())
    for term in _COMPOUND:
        if term.replace("-", " ") in flat:
            found.add(term)

    return {canonical(t) for t in found}
