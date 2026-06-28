from jobhunt.agents.resume import (
    ResumeArchitectAgent,
    ResumeInputs,
    extract_keywords,
)
from jobhunt.models import JobPosting


def test_extract_keywords_drops_stopwords():
    kws = extract_keywords(
        "We are looking for Python and Kubernetes experience to build APIs.",
        limit=5,
    )
    assert "python" in kws and "kubernetes" in kws
    assert "the" not in kws and "are" not in kws


def test_resume_bullets_have_evidence_ids(profile, store, bus):
    posting = JobPosting(
        job_id="j1", source="g", source_id="1",
        url="https://boards.greenhouse.io/co/j1",
        title="Backend Engineer", company="C",
        location="Remote", jd_text="Python, Kubernetes, PostgreSQL, Redis.",
        remote=True,
    )
    agent = ResumeArchitectAgent(store, bus)
    res = agent.run(
        ResumeInputs(profile=profile, postings=[posting]), task_id="t"
    )
    [doc] = res.output
    # Every bullet must be backed by an evidence node.
    assert doc.bullets and all(b.get("evidence_id") for b in doc.bullets)
    # Self-critique flags no-hallucination at 1.0.
    assert res.trace.self_critique.get("no_hallucination") == 1.0
    # Human approval gate is on by default.
    assert doc.requires_human_approval is True


def test_resume_bullets_polished_by_llm_keep_evidence_id(profile, store, bus):
    posting = JobPosting(
        job_id="j3", source="g", source_id="3",
        url="https://boards.greenhouse.io/co/j3",
        title="Backend Engineer", company="C",
        location="Remote", jd_text="Python, Kubernetes.",
        remote=True,
    )

    def llm(action, payload):
        assert action == "rewrite_bullet"
        return f"LLM polished: {payload['keyword']}"

    agent = ResumeArchitectAgent(store, bus, llm=llm)
    res = agent.run(
        ResumeInputs(profile=profile, postings=[posting]), task_id="t"
    )
    [doc] = res.output
    assert doc.bullets and all(b.get("evidence_id") for b in doc.bullets)
    assert any(b["text"].startswith("LLM polished:") for b in doc.bullets)


def test_resume_bullets_fall_back_when_llm_raises(profile, store, bus):
    posting = JobPosting(
        job_id="j4", source="g", source_id="4",
        url="https://boards.greenhouse.io/co/j4",
        title="Backend Engineer", company="C",
        location="Remote", jd_text="Python, Kubernetes.",
        remote=True,
    )

    def broken_llm(action, payload):
        raise RuntimeError("rate limited")

    agent = ResumeArchitectAgent(store, bus, llm=broken_llm)
    res = agent.run(
        ResumeInputs(profile=profile, postings=[posting]), task_id="t"
    )
    [doc] = res.output
    # Deterministic phrasing survives the LLM failure, evidence stays intact.
    assert doc.bullets and all(b.get("evidence_id") for b in doc.bullets)
    assert all("Delivered work involving" in b["text"] for b in doc.bullets)


def test_resume_refines_when_coverage_low(profile, store, bus):
    # An unrelated JD should drive coverage low; ensure the agent still
    # produces structured output without hallucinations (the safety
    # invariant), even if quality remains below threshold.
    posting = JobPosting(
        job_id="j2", source="g", source_id="2",
        url="x", title="Sales", company="C",
        location="Remote", jd_text="Quota, channel, enterprise sales, SaaS.",
        remote=True,
    )
    agent = ResumeArchitectAgent(store, bus)
    res = agent.run(
        ResumeInputs(profile=profile, postings=[posting]), task_id="t"
    )
    assert res.output is not None
    # No hallucinations even at low coverage.
    assert all(
        b.get("evidence_id") for d in res.output for b in d.bullets
    )
