from jobhunt.adapters import FixtureSource
from jobhunt.agents.discovery import (
    DiscoveryAgent,
    DiscoveryInputs,
    dedupe,
    ghost_score,
    relevance,
)
from jobhunt.models import JobPosting


def test_dedupe_preserves_first_occurrence_order():
    a = JobPosting(job_id="1", source="x", source_id="a", url="", title="T",
                   company="C", location="L", jd_text="")
    b = JobPosting(job_id="2", source="y", source_id="b", url="", title="T",
                   company="C", location="L", jd_text="")
    c = JobPosting(job_id="3", source="z", source_id="c", url="", title="U",
                   company="C", location="L", jd_text="")
    out = dedupe([a, b, c])
    assert [p.job_id for p in out] == ["1", "3"]


def test_ghost_score_flags_old_or_short_jds():
    fresh = JobPosting(job_id="1", source="x", source_id="1", url="", title="t",
                       company="c", location="l", jd_text="x" * 500,
                       posted_at=1e18)
    stale = JobPosting(job_id="2", source="x", source_id="2", url="", title="t",
                       company="c2", location="l", jd_text="short",
                       posted_at=1.0)  # epoch 1970 → ancient
    assert ghost_score(fresh) == 0.0
    assert ghost_score(stale) > 0.5


def test_relevance_higher_for_aligned_jd(profile):
    p_match = JobPosting(
        job_id="1", source="x", source_id="1", url="", title="Backend Engineer",
        company="c", location="l",
        jd_text="Python, Kubernetes, PostgreSQL, Redis, FastAPI, distributed.",
    )
    p_noise = JobPosting(
        job_id="2", source="x", source_id="2", url="", title="Sales Director",
        company="c", location="l",
        jd_text="Enterprise sales, quota carrying, channel partners.",
    )
    assert relevance(p_match, profile) > relevance(p_noise, profile)


def test_discovery_runs_against_fixture_sources(profile, store, bus, all_sources):
    agent = DiscoveryAgent(store, bus)
    queries = [{
        "role": "backend engineer",
        "location": "",
        "remote_ok": True,
        "skills": profile.skills[:3],
        "exclude_companies": profile.veto_companies,
    }]
    result = agent.run(
        DiscoveryInputs(profile=profile, queries=queries,
                        sources=all_sources, plan_id="p", weekly_target=10),
        task_id="t",
    )
    batch = result.output
    assert batch is not None
    assert batch.postings, "expected at least one posting"
    # Acme appears in both greenhouse and linkedin fixtures — must be deduped.
    companies = [p.company for p in batch.postings]
    assert companies.count("Acme Robotics") == 1
    # Vetoed companies must not appear.
    assert "Fabrikam" not in companies
    # Postings are ranked by relevance desc.
    scores = [p.relevance_score for p in batch.postings]
    assert scores == sorted(scores, reverse=True)


def test_discovery_marks_source_degraded_when_down(profile, store, bus):
    sources = [
        FixtureSource(name="greenhouse", only_sources=["greenhouse"]),
        FixtureSource(name="linkedin", unavailable=True),
    ]
    agent = DiscoveryAgent(store, bus)
    queries = [{"role": "backend engineer", "location": "", "remote_ok": True,
                "skills": [], "exclude_companies": []}]
    result = agent.run(
        DiscoveryInputs(profile=profile, queries=queries, sources=sources,
                        plan_id="p"),
        task_id="t",
    )
    assert result.output is not None
    assert "linkedin" in result.output.degraded_sources
    assert result.degraded is True
