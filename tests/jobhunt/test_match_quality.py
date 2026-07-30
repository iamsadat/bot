"""Regressions for the reported bug: "most roles are senior, top match is 12%".

Every case here comes from a real board response measured against a real
résumé — a junior data engineer in Hyderabad. Before these fixes the scorer's
maximum over a whole Arbeitnow board was 0.136 with a median of 0.011, and a
German tax-advisory listing ranked alongside engineering roles.
"""

from __future__ import annotations

import pytest

from jobhunt.adapters.filters import (
    country_of,
    passes_local_filters,
    place_matches,
    remote_scope,
    title_matches_role,
)
from jobhunt.agents.discovery import location_fit, score, skill_fit, title_fit
from jobhunt.models import JobPosting, UserProfile
from jobhunt.seniority import (
    JUNIOR,
    MID,
    SENIOR,
    STAFF,
    fit,
    level_from_posting,
    level_from_profile,
    level_from_title,
    rank_in_title,
    within_band,
    years_in_title,
)

JUNIOR_DATA_JD = """
We are hiring a Data Engineer to join our platform team in Hyderabad.
You will build and maintain ETL pipelines using PySpark on Databricks,
write SQL transformations, and orchestrate workflows with Airflow.
Our stack is Python, PostgreSQL, Docker and Kubernetes, deployed through
a CI/CD pipeline. Experience with Golang is a plus. You will collaborate
with analytics engineers to model data in our warehouse and validate
outputs against source systems. We care about testing and observability,
and everything runs on containers orchestrated in production clusters.
This is a great role for somebody early in their data engineering career
who wants to learn distributed computing on real workloads at scale.
"""

TAX_ADVISER_JD = """
Steuerberater (m/w/d) gesucht. Wir suchen eine erfahrene Person fuer die
Beratung unserer Mandanten in allen steuerlichen Fragen. Sie erstellen
Jahresabschluesse und Steuererklaerungen, betreuen Betriebspruefungen und
entwickeln steuerliche Gestaltungen. Wir bieten ein modernes Buero, flexible
Arbeitszeiten und ein Gehalt ab 90.000 Euro. Kenntnisse in DATEV und Excel
werden vorausgesetzt. Eine Partnerperspektive ist auf Wunsch moeglich, und
wir unterstuetzen Ihre Weiterbildung mit einem grosszuegigen Budget.
"""


def _profile(**over) -> UserProfile:
    base = dict(
        user_id="u",
        name="Sadat",
        email="s@example.com",
        target_roles=["Data Engineer", "Backend Engineer"],
        locations=["Hyderabad", "India", "Remote"],
        skills=[
            "python", "sql", "pyspark", "databricks", "etl", "golang",
            "docker", "kubernetes", "postgresql", "airflow",
        ],
        experience_years=1,
    )
    base.update(over)
    return UserProfile(**base)


def _posting(**over) -> JobPosting:
    base = dict(
        job_id="j1", source="greenhouse", source_id="1",
        url="https://job-boards.greenhouse.io/acme/jobs/1",
        title="Data Engineer", company="Acme", location="Hyderabad, India",
        jd_text=JUNIOR_DATA_JD, remote=False,
    )
    base.update(over)
    return JobPosting(**base)


# --------------------------------------------------------------------------- #
# The headline numbers
# --------------------------------------------------------------------------- #

def test_matching_job_scores_well_above_the_old_ceiling():
    """A genuinely suitable role must read as a strong match, not as 12%."""
    result = score(_posting(), _profile())
    assert result["total"] > 0.6, result


def test_irrelevant_job_scores_low_however_long_its_description():
    """The tax-advisory listing that used to rank beside engineering roles."""
    result = score(
        _posting(title="Steuerberater (m/w/d) in Remseck am Neckar",
                 location="Remseck am Neckar", jd_text=TAX_ADVISER_JD),
        _profile(),
    )
    assert result["total"] < 0.3, result
    assert result["title"] == 0.0


def test_relevant_job_outranks_irrelevant_one_by_a_wide_margin():
    good = score(_posting(), _profile())["total"]
    bad = score(
        _posting(title="SMB Account Executive, Germany", location="Berlin",
                 jd_text=TAX_ADVISER_JD),
        _profile(),
    )["total"]
    assert good - bad > 0.35, (good, bad)


def test_score_breakdown_explains_itself():
    """The UI shows every component, so all of them have to be present."""
    result = score(_posting(), _profile())
    for key in ("title", "skills", "seniority", "location", "total"):
        assert 0.0 <= result[key] <= 1.0
    assert "pyspark" in result["matched_keywords"] or "spark" in result["matched_keywords"]
    assert result["candidate_level_name"] == "junior"


# --------------------------------------------------------------------------- #
# Skill coverage — the part that silently returned 0.00 for every job
# --------------------------------------------------------------------------- #

def test_skill_fit_finds_the_technologies_a_jd_names():
    ratio, matched, _ = skill_fit(JUNIOR_DATA_JD, ["python", "pyspark", "databricks"])
    assert ratio > 0
    assert "databricks" in matched


def test_skill_fit_ignores_jd_prose():
    """Frequency ranking surfaced "benefits" and "culture" as required skills."""
    _, matched, missing = skill_fit(JUNIOR_DATA_JD, ["python"])
    for word in ("benefits", "culture", "ownership", "training", "team"):
        assert word not in matched + missing


def test_skill_names_that_are_english_words_need_their_own_spelling():
    """Lowercase matching read "we go fast" as Golang."""
    ratio, matched, _ = skill_fit(
        "We go fast and we care deeply about our customers. " * 20, ["golang"]
    )
    assert "golang" not in matched


def test_synonyms_count_as_the_same_skill():
    ratio, matched, _ = skill_fit(
        "Deploy onto k8s clusters with terraform and helm. " * 12, ["kubernetes"]
    )
    assert "kubernetes" in matched


# --------------------------------------------------------------------------- #
# Title matching
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("title,role,expected", [
    ("Data Engineer", "data engineer", True),
    ("Junior Data Engineer", "data engineer", True),
    ("Senior Data Platform Engineer (m/w/d)", "data engineer", True),
    ("Backend Developer", "backend engineer", True),
    ("Go Engineer", "golang engineer", True),
    # The two that wrongly matched via the job description.
    ("Steuerberater (m/w/d) in Remseck", "data engineer", False),
    ("SMB Account Executive, Germany", "data engineer", False),
    # Same field, different job family.
    ("Data Analyst", "data engineer", False),
    ("Data Scientist", "data engineer", False),
    ("Frontend Engineer", "backend engineer", False),
])
def test_title_matches_role(title, role, expected):
    assert title_matches_role(title, role) is expected


def test_title_fit_penalises_a_generic_only_overlap():
    """Sharing only the word "engineer" is not a role match."""
    assert title_fit("Senior Marketing Engineer", ["data engineer"]) < 0.3


# --------------------------------------------------------------------------- #
# Seniority
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("title,level", [
    ("Senior Staff Software Engineer", STAFF),
    ("Junior Data Engineer (Former Data Engineering Intern)", JUNIOR),
    ("(Senior) Data Platform Engineer (m/w/d)", SENIOR),
    ("Werkstudent Data Analytics", 0),
    ("Graduate Software Engineer", JUNIOR),
    ("Data Platform Engineer", None),
])
def test_level_from_title(title, level):
    assert level_from_title(title) == level


@pytest.mark.parametrize("title,level", [
    # Numeric ladders: an SDE III used to count as unmarked and score 100%.
    ("Software Development Engineer III DevOps", SENIOR),
    ("Software Development Engineer IV Android", STAFF),
    ("Software Engineer 3", SENIOR),
    ("Software Engineer (L1)", JUNIOR),
    # Years written into the title, ubiquitous on Indian boards.
    ("Site Reliability Engineer - Big Data (7 to 11 years)", SENIOR),
    ("Business Intelligence Engineer (2-4 years)", MID),
    ("Software Engineer, React Native (3-5 Years)", MID),
])
def test_level_from_posting_reads_ranks_and_years(title, level):
    assert level_from_posting(_posting(title=title)) == level


def test_declared_seniority_beats_the_title():
    posting = _posting(title="Software Engineer", raw={"seniority": ["Entry-level", "Mid-level"]})
    assert level_from_posting(posting) == JUNIOR


def test_years_in_title_takes_the_floor_of_a_range():
    assert years_in_title("Engineer (4 to 8 years)") == 4
    assert years_in_title("Data Engineer") is None


def test_rank_ignores_titles_without_a_ladder():
    assert rank_in_title("Software Engineer, Android") is None


def test_unknown_level_is_neutral_not_a_penalty():
    """Most titles carry no level marker; penalising them buries the board."""
    assert fit(JUNIOR, None) == 1.0
    assert within_band(JUNIOR, None) is True


def test_staff_roles_are_out_of_band_for_a_junior():
    assert within_band(JUNIOR, STAFF) is False
    assert within_band(JUNIOR, MID) is True


def test_profile_level_comes_from_measured_years():
    assert level_from_profile(_profile(experience_years=1)) == JUNIOR
    assert level_from_profile(_profile(experience_years=7)) == SENIOR


def test_seniority_gate_rejects_over_levelled_roles():
    query = {
        "role": "data engineer", "location": "", "remote_ok": True,
        "exclude_companies": [], "candidate_level": JUNIOR,
    }
    assert passes_local_filters(_posting(title="Data Engineer"), query) is True
    assert passes_local_filters(_posting(title="Staff Data Engineer"), query) is False
    assert passes_local_filters(_posting(title="Engineering Manager - Data"), query) is False


# --------------------------------------------------------------------------- #
# Location — the substring bugs that put US-only jobs on an Indian board
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("wanted,text,expected", [
    ("india", "Hyderabad, India", True),
    # Boards post a bare city; 201 of 202 postings were dropped for want of this.
    ("india", "Bangalore", True),
    ("india", "Hybrid in Bengaluru", True),
    ("united states", "Seattle", True),
    ("us", "United States", True),
    # "India" is a substring of "Indiana".
    ("india", "Remote - Indiana, USA", False),
    # Two cities in one country are still two different cities.
    ("hyderabad", "Bangalore", False),
    ("india", "San Francisco", False),
])
def test_place_matches(wanted, text, expected):
    assert place_matches(wanted, text) is expected


def test_country_of_resolves_cities():
    assert country_of("Bengaluru") == "india"
    assert country_of("Remote") is None


@pytest.mark.parametrize("location,expected", [
    ("Remote US", ["us"]),
    ("Remote - India", ["india"]),
    ("Remote", []),
    # A city plus a remote flag means remote-from-there, not remote-anywhere.
    ("San Francisco", ["san francisco"]),
])
def test_remote_scope_is_read_from_the_location_text(location, expected):
    assert remote_scope(_posting(location=location, remote=True)) == expected


def test_structured_remote_scope_wins():
    posting = _posting(location="Remote", remote=True,
                       raw={"remote_scope": ["India", "Singapore"]})
    assert remote_scope(posting) == ["india", "singapore"]


def test_remote_role_closed_to_the_candidates_country_is_filtered_out():
    query = {
        "role": "", "location": "hyderabad", "remote_ok": True,
        "exclude_companies": [], "all_locations": ["Hyderabad", "India"],
    }
    reachable = _posting(location="Remote - India", remote=True)
    closed = _posting(location="Remote US", remote=True)
    assert passes_local_filters(reachable, query) is True
    assert passes_local_filters(closed, query) is False


def test_listing_remote_does_not_open_every_remote_job():
    """"Remote" as a preference still respects a posting's own scope."""
    query = {
        "role": "", "location": "remote", "remote_ok": True,
        "exclude_companies": [], "all_locations": ["Hyderabad", "India", "Remote"],
    }
    argentina_only = _posting(location="Remote", remote=True,
                              raw={"remote_scope": ["Argentina"]})
    assert passes_local_filters(argentina_only, query) is False


def test_location_fit_does_not_reward_an_unreachable_remote_role():
    here = location_fit(_posting(location="Bengaluru, India"), _profile())
    elsewhere = location_fit(
        _posting(location="Remote US", remote=True), _profile())
    assert here == 1.0
    assert elsewhere < 0.3


# --------------------------------------------------------------------------- #
# Thin descriptions must not be punished (Adzuna returns ~200-char snippets)
# --------------------------------------------------------------------------- #

def test_a_snippet_length_description_is_not_counted_against_a_job():
    snippet = _posting(jd_text="Data Engineer wanted in Hyderabad. Apply now.")
    full = _posting()
    assert score(snippet, _profile())["skills_scored"] is False
    assert score(full, _profile())["skills_scored"] is True
    # Absent evidence must not sink an otherwise strong match.
    assert score(snippet, _profile())["total"] > 0.5


def test_matching_none_of_a_short_skill_list_still_counts_against_a_job():
    """A long Android JD naming only "android" and "kotlin" scored 82% for a
    candidate with neither, because the list was too short to be "scored"."""
    android_jd = (
        "We are hiring an Android engineer. You will build our Kotlin "
        "application and own the Android release process. " * 12
    )
    result = score(_posting(title="Software Engineer, Android", jd_text=android_jd),
                   _profile())
    assert result["skills_scored"] is True
    assert result["skills"] == 0.0
    assert result["total"] < 0.7, result


def test_a_snippet_that_does_name_skills_still_gets_credit():
    with_skills = _posting(jd_text="PySpark and Databricks engineer, Hyderabad.")
    without = _posting(jd_text="Engineer wanted. Great team, great benefits.")
    assert (score(with_skills, _profile())["total"]
            > score(without, _profile())["total"])
