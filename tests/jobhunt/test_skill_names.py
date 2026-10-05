import json

from jobhunt import skill_names
from jobhunt.models import JobPosting, UserProfile
from jobhunt.resume_template import build_tailored_resume

PARSED = ["airflow", "amazon s3", "apache flink", "apache kafka", "apache spark", "aws",
          "c", "cassandra", "data quality", "databricks", "delta lake", "docker", "elt",
          "etl", "git", "kafka", "linux", "medallion architecture", "postgresql",
          "pyspark", "python", "rest", "rest apis", "sas", "scd type 2", "silver",
          "snowflake", "spark", "spark sql", "sql", "unity catalog", "z-order", "dbt"]


def test_duplicates_and_fragments_go():
    keys = skill_names.clean(PARSED)
    assert "silver" not in keys and "z-order" not in keys
    assert keys.count("apache spark") == 1 and "spark" not in keys
    assert keys.count("apache kafka") == 1 and keys.count("rest apis") == 1
    assert "etl/elt" in keys and "elt" not in keys


def test_grouped_with_proper_names():
    groups = dict(skill_names.grouped(PARSED))
    assert groups["Languages"][:2] == ["Python", "SQL"]
    assert "Apache Spark" in groups["Big Data & Streaming"]
    assert "PySpark" in groups["Big Data & Streaming"]
    assert "Apache Airflow" in groups["Orchestration & Integration"]
    assert "Amazon S3" in groups["Cloud & DevOps"]
    assert "ETL/ELT" in groups["Data Engineering"]
    assert "SCD Type 2" in groups["Data Engineering"]
    assert all(n[0].isupper() or n == "dbt" for names in groups.values() for n in names)


def test_jd_skills_lead_each_group():
    groups = dict(skill_names.grouped(PARSED, "We use Snowflake and Kafka daily."))
    assert groups["Data Platforms"][0] == "Snowflake"
    assert groups["Big Data & Streaming"][0] == "Apache Kafka"


def _profile():
    return UserProfile(
        user_id="u", name="Ada Lovelace", email="ada@example.com",
        target_roles=["Data Engineer"], locations=["Hyderabad"], skills=PARSED,
        experiences=[{"title": "Data Engineer", "company": "Acme", "start": "2025",
                      "end": "Present",
                      "bullets": ["Built Spark pipelines on Databricks for 150M records.",
                                  "Migrated 150 SAS files to PySpark."]}],
        projects=[{"name": "Crypto Stream", "skills": ["kafka", "pyspark"],
                   "bullets": ["Streamed market data through Kafka into Cassandra."]}],
    )


def _posting():
    return JobPosting(job_id="j", source="t", source_id="j", company="Point72",
                      title="Data Reliability Engineer", location="Hyderabad",
                      url="https://x", jd_text="Python, Spark, Kafka, Airflow, SQL Server.")


def test_tailored_resume_reads_like_a_resume():
    draft = build_tailored_resume(_profile(), _posting())
    assert "Ada" not in draft.summary and "Point72" not in draft.summary
    assert draft.summary.startswith("Data Engineer")
    skills = next(s for s in draft.sections if s.kind == "skills").body
    assert skills.splitlines()[0].startswith("Languages: Python")
    assert "silver" not in skills.lower()
    projects = next(s for s in draft.sections if s.kind == "projects")
    assert projects.rows[0]["left"] == "**Crypto Stream**, Apache Kafka, PySpark"


def test_entry_bullets_polished_together_and_fall_back():
    calls = []

    def llm(action, payload):
        calls.append(action)
        if action == "rewrite_bullets":
            return json.dumps([b.replace("Built", "Engineered") for b in payload["bullets"]])
        return ""

    draft = build_tailored_resume(_profile(), _posting(), llm=llm)
    texts = [b.text for b in draft.all_bullets()]
    assert any(t.startswith("Engineered Spark") for t in texts)
    assert calls.count("rewrite_bullets") == 2  # one per entry, not per bullet
    assert draft.summary.startswith("Data Engineer")  # LLM summary empty → template
