"""Presentable skill names for the résumé.

Profiles store skills lowercased, in whatever form the résumé parser found
them, because that is what matching wants. A résumé shown to a recruiter
needs the opposite: one proper-cased name per skill ("Apache Spark", not
"spark" and "apache spark"), no fragments that only make sense next to their
neighbours ("silver" from "Bronze/Silver/Gold"), and the list grouped the way
a skills section is read.
"""

from __future__ import annotations

import re

# Surface forms that name the same skill → the one key used below.
_SAME_AS: dict[str, str] = {
    "spark": "apache spark", "apache-spark": "apache spark",
    "kafka": "apache kafka", "flink": "apache flink",
    "airflow": "apache airflow", "apache-airflow": "apache airflow",
    "beam": "apache beam", "hive": "apache hive",
    "rest": "rest apis", "restful": "rest apis", "rest api": "rest apis",
    "restful apis": "rest apis", "restful api": "rest apis",
    "s3": "amazon s3", "aws s3": "amazon s3",
    "postgres": "postgresql", "psql": "postgresql",
    "golang": "go", "k8s": "kubernetes", "mongo": "mongodb",
    "node": "node.js", "nodejs": "node.js", "reactjs": "react",
    "nextjs": "next.js", "cpp": "c++", "csharp": "c#",
    "ci-cd": "ci/cd", "cicd": "ci/cd",
    "structured streaming": "spark structured streaming",
    "spark-sql": "spark sql", "delta-lake": "delta lake",
    "unity-catalog": "unity catalog", "power-bi": "power bi", "powerbi": "power bi",
    "scikit-learn": "scikit-learn", "sklearn": "scikit-learn",
    "github-actions": "github actions", "sql-server": "sql server",
    "ms sql server": "sql server", "mssql": "sql server",
    "aws-glue": "aws glue", "azure-data-factory": "azure data factory",
    "adf": "azure data factory", "data-modeling": "data modeling",
    "data modelling": "data modeling", "medallion": "medallion architecture",
    "scd2": "scd type 2", "scd type-2": "scd type 2", "scd": "scd type 2",
}

# Fragments, not skills: Medallion layer names split out of
# "Bronze/Silver/Gold", Delta table commands, and words the vocabulary scan
# picks out of prose.
_NOT_A_SKILL: frozenset[str] = frozenset({
    "bronze", "silver", "gold", "bronze layer", "silver layer", "gold layer",
    "z-order", "zorder", "z-ordering", "optimize", "vacuum", "merge",
    "distributed", "pipelines", "orchestration", "containers",
    "notebooks", "databricks notebooks", "jupyter notebooks",
})

# Exact display names where title-casing would be wrong.
_DISPLAY: dict[str, str] = {
    "python": "Python", "sql": "SQL", "java": "Java", "scala": "Scala",
    "javascript": "JavaScript", "typescript": "TypeScript", "go": "Go",
    "c": "C", "c++": "C++", "c#": "C#", "r": "R", "bash": "Bash",
    "shell": "Shell scripting", "sas": "SAS", "pl/sql": "PL/SQL", "t-sql": "T-SQL",
    "apache spark": "Apache Spark", "pyspark": "PySpark", "spark sql": "Spark SQL",
    "spark structured streaming": "Spark Structured Streaming",
    "apache kafka": "Apache Kafka", "apache flink": "Apache Flink",
    "apache airflow": "Apache Airflow", "apache beam": "Apache Beam",
    "apache hive": "Apache Hive", "hadoop": "Hadoop", "kinesis": "Amazon Kinesis",
    "databricks": "Databricks", "snowflake": "Snowflake", "delta lake": "Delta Lake",
    "unity catalog": "Unity Catalog", "bigquery": "BigQuery", "redshift": "Amazon Redshift",
    "synapse": "Azure Synapse", "iceberg": "Apache Iceberg", "lakehouse": "Lakehouse",
    "dbt": "dbt", "informatica idmc": "Informatica IDMC", "informatica": "Informatica",
    "fivetran": "Fivetran", "airbyte": "Airbyte", "aws glue": "AWS Glue",
    "azure data factory": "Azure Data Factory", "ssis": "SSIS", "talend": "Talend",
    "dagster": "Dagster", "prefect": "Prefect", "nifi": "Apache NiFi",
    "postgresql": "PostgreSQL", "mysql": "MySQL", "mongodb": "MongoDB",
    "cassandra": "Apache Cassandra", "redis": "Redis", "sql server": "SQL Server",
    "oracle": "Oracle", "dynamodb": "DynamoDB", "elasticsearch": "Elasticsearch",
    "sqlite": "SQLite", "clickhouse": "ClickHouse", "duckdb": "DuckDB",
    "aws": "AWS", "amazon s3": "Amazon S3", "gcp": "GCP", "azure": "Azure",
    "emr": "Amazon EMR", "athena": "Amazon Athena", "lambda": "AWS Lambda",
    "docker": "Docker", "kubernetes": "Kubernetes", "terraform": "Terraform",
    "git": "Git", "github": "GitHub", "gitlab": "GitLab", "linux": "Linux",
    "ci/cd": "CI/CD", "github actions": "GitHub Actions", "jenkins": "Jenkins",
    "etl": "ETL", "elt": "ELT", "scd type 2": "SCD Type 2",
    "rest apis": "REST APIs", "graphql": "GraphQL", "grpc": "gRPC",
    "fastapi": "FastAPI", "django": "Django", "flask": "Flask",
    "node.js": "Node.js", "react": "React", "next.js": "Next.js",
    "html": "HTML", "css": "CSS", "pandas": "pandas", "numpy": "NumPy",
    "scikit-learn": "scikit-learn", "pytorch": "PyTorch", "tensorflow": "TensorFlow",
    "power bi": "Power BI", "tableau": "Tableau", "looker": "Looker",
    "excel": "Excel", "jira": "Jira", "parquet": "Parquet", "avro": "Avro",
    "llm": "LLMs", "rag": "RAG", "nosql": "NoSQL", "api": "APIs",
}

_LANGUAGES = ["python", "sql", "java", "scala", "javascript", "typescript", "go",
              "c", "c++", "c#", "r", "bash", "shell", "sas", "pl/sql", "t-sql",
              "rust", "kotlin", "ruby", "php", "swift"]
_BIG_DATA = ["apache spark", "pyspark", "spark sql", "spark structured streaming",
             "apache kafka", "apache flink", "apache beam", "apache hive", "hadoop",
             "kinesis", "pandas", "numpy"]
_PLATFORMS = ["databricks", "databricks jobs", "snowflake", "delta lake", "unity catalog", "bigquery",
              "redshift", "synapse", "iceberg", "lakehouse", "emr", "athena", "parquet",
              "avro"]
_PIPELINE_TOOLS = ["apache airflow", "dbt", "informatica idmc", "informatica",
                   "fivetran", "airbyte", "aws glue", "azure data factory", "ssis",
                   "talend", "dagster", "prefect", "nifi"]
_DATABASES = ["postgresql", "mysql", "mongodb", "cassandra", "redis", "sql server",
              "oracle", "dynamodb", "elasticsearch", "sqlite", "clickhouse", "duckdb",
              "nosql"]
_CLOUD = ["aws", "amazon s3", "gcp", "azure", "lambda", "docker", "kubernetes",
          "terraform", "git", "github", "gitlab", "linux", "ci/cd", "github actions",
          "jenkins"]
_PRACTICES = ["etl", "elt", "medallion architecture", "scd type 2", "data modeling",
              "data warehousing", "lakehouse architecture", "batch processing",
              "stream processing", "partitioning"]

# (heading, members) in the order a data résumé is read. Anything unplaced
# lands in "Other".
# Members are listed most prominent first; a group keeps that order.
_CATEGORIES: list[tuple[str, list[str]]] = [
    ("Languages", _LANGUAGES),
    ("Big Data & Streaming", _BIG_DATA),
    ("Data Platforms", _PLATFORMS),
    ("Orchestration & Integration", _PIPELINE_TOOLS),
    ("Databases", _DATABASES),
    ("Cloud & DevOps", _CLOUD),
    ("Data Engineering", _PRACTICES),
]


def key(skill: str) -> str:
    """The merge key for a stored skill, or "" when it is not a skill."""
    k = " ".join(str(skill).strip().lower().split())
    k = _SAME_AS.get(k, k)
    return "" if not k or k in _NOT_A_SKILL else k


def display(skill: str) -> str:
    """Proper-cased name for a skill ("apache spark" → "Apache Spark")."""
    k = key(skill) or " ".join(str(skill).split()).lower()
    if k in _DISPLAY:
        return _DISPLAY[k]
    k = k.replace("-", " ")  # taxonomy spelling: "data-warehouse"
    if k in _DISPLAY:
        return _DISPLAY[k]
    return " ".join(w if any(c.isupper() for c in w) else w.capitalize()
                    for w in k.split())


def _category(k: str) -> str:
    for heading, members in _CATEGORIES:
        if k in members:
            return heading
    if k.startswith("data ") or k.endswith(" architecture"):
        return "Data Engineering"
    return "Other"


def _mentioned(k: str, jd_low: str) -> bool:
    forms = {k, display(k).lower()} | {s for s, t in _SAME_AS.items() if t == k}
    return any(re.search(rf"(?<![\w]){re.escape(f)}(?![\w])", jd_low) for f in forms)


def clean(skills: list[str]) -> list[str]:
    """Merge keys of ``skills``: duplicates and fragments removed, order kept."""
    out = list(dict.fromkeys(k for k in map(key, skills) if k))
    # "ETL" and "ELT" read as one practice.
    if "etl" in out and "elt" in out:
        out.remove("elt")
        out[out.index("etl")] = "etl/elt"
    return out


def grouped(skills: list[str], jd: str = "") -> list[tuple[str, list[str]]]:
    """Display names grouped by category. Within a group, skills the job
    description names come first, so the first thing a recruiter reads in
    each line is what they asked for."""
    jd_low = (jd or "").lower()
    groups: dict[str, list[str]] = {}
    for k in clean(skills):
        groups.setdefault(_category(k.split("/")[0]), []).append(k)
    order = [h for h, _ in _CATEGORIES] + ["Other"]
    out: list[tuple[str, list[str]]] = []
    for heading in order:
        members = groups.get(heading)
        if not members:
            continue
        rank = dict((m, i) for i, m in enumerate(dict(_CATEGORIES).get(heading, [])))
        rank["etl/elt"] = rank.get("etl", 0)
        members = sorted(members, key=lambda k: (bool(jd_low) and not _mentioned(k, jd_low),
                                                 rank.get(k, len(rank))))
        names = ["ETL/ELT" if k == "etl/elt" else display(k) for k in members]
        out.append((heading, names))
    return out


def skills_block(skills: list[str], jd: str = "") -> str:
    """The résumé skills section: one "Category: a, b, c" line per group."""
    return "\n".join(f"{h}: {', '.join(names)}" for h, names in grouped(skills, jd))
