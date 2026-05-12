"""Tests for the inter-agent peer critique (Phase 2).

The Vetting Agent reviews a tailored résumé against the JD's culture vector,
keyword density and evidence diversity. Validates that the verdict is
consistent with the score, and that flags surface real problems.
"""

from __future__ import annotations

from jobhunt.agents.peer_critique import (
    PeerCritiqueAgent,
    PeerCritiqueInputs,
    detect_culture,
)
from jobhunt.models import JobPosting, UserProfile
from jobhunt.resume_template import build_resume_draft
from jobhunt.trace import ThoughtBus, TraceStore


def _profile_polyglot() -> UserProfile:
    return UserProfile(
        user_id="u-1",
        name="Ada Lovelace",
        email="ada@example.com",
        target_roles=["backend engineer"],
        locations=["Remote"],
        skills=["python", "kubernetes", "postgres", "ownership", "ship"],
        experiences=[
            {"company": "Globex", "highlight": "Built distributed Python services."},
            {"company": "Initech", "highlight": "Led platform team; introduced OpenTelemetry."},
        ],
    )


def _startup_posting() -> JobPosting:
    return JobPosting(
        job_id="j-startup",
        source="ashby",
        source_id="s-1",
        url="https://startup.example/jobs/1",
        title="Founding Engineer",
        company="StartupCo",
        location="Remote",
        jd_text=(
            "Scrappy startup looking for 0-to-1 ownership. You'll ship Python "
            "services fast and iterate. Wear-many-hats culture."
        ),
    )


def _enterprise_posting() -> JobPosting:
    return JobPosting(
        job_id="j-corp",
        source="greenhouse",
        source_id="g-1",
        url="https://corp.example/jobs/1",
        title="Staff Engineer",
        company="MegaCorp",
        location="Remote",
        jd_text=(
            "Drive stakeholder alignment across compliance, governance and "
            "policy frameworks. SOC2 experience preferred. Python."
        ),
    )


def test_detect_culture_classifies_buckets_correctly():
    startup = detect_culture(
        "scrappy ownership ship fast iterate 0-to-1 founding"
    )
    assert startup["startup"] > startup["enterprise"]
    assert startup["startup"] > startup["research"]

    corp = detect_culture(
        "stakeholder alignment compliance governance policy framework"
    )
    assert corp["enterprise"] > corp["startup"]
    assert corp["enterprise"] > corp["research"]


def test_detect_culture_handles_empty_text():
    result = detect_culture("")
    assert all(v == 0.0 for v in result.values())


def _run_critique(posting: JobPosting, profile: UserProfile, *, kws: list[str]):
    draft = build_resume_draft(profile, posting, keywords=kws)
    agent = PeerCritiqueAgent(TraceStore(), ThoughtBus())
    result = agent.run(
        PeerCritiqueInputs(
            draft=draft,
            posting=posting,
            document_id="doc-1",
            required_keywords=kws,
        ),
        task_id="t-1",
    )
    assert result.output is not None
    return result.output


def test_startup_match_gets_ship_verdict():
    profile = _profile_polyglot()
    posting = _startup_posting()
    critique = _run_critique(
        posting, profile, kws=["python", "ownership", "ship"],
    )
    assert critique.score >= 0.5
    assert critique.verdict in {"ship", "hold"}
    assert critique.keyword_density >= 0.5


def test_enterprise_jd_with_startup_resume_flags_culture_mismatch():
    # Build a résumé that only carries startup phrasing.
    profile = UserProfile(
        user_id="u-2", name="Eve", email="eve@example.com",
        target_roles=["staff engineer"], locations=["Remote"],
        skills=["python", "ship", "scrappy", "ownership"],
        experiences=[{"highlight": "Ship features fast in a scrappy team."}],
    )
    posting = _enterprise_posting()
    critique = _run_critique(
        posting, profile, kws=["python", "compliance", "governance"],
    )
    assert any("culture_mismatch" in f for f in critique.flags)
    assert critique.verdict in {"hold", "rework"}


def test_low_density_flag_triggers_suggestions():
    profile = _profile_polyglot()
    posting = _enterprise_posting()
    critique = _run_critique(
        posting, profile,
        kws=["python", "compliance", "soc2", "governance", "audit"],
    )
    assert critique.keyword_density < 1.0
    assert any("low_keyword_density" in f for f in critique.flags)
    assert critique.suggestions


def test_evidence_diversity_flag_when_bullets_repeat_experience():
    profile = UserProfile(
        user_id="u-3", name="Ada", email="ada@example.com",
        target_roles=["backend"], locations=["Remote"],
        skills=["python"],
        experiences=[
            {"highlight": "Did kubernetes python redis postgres fastapi work."},
        ],
    )
    posting = _enterprise_posting()
    critique = _run_critique(
        posting, profile,
        kws=["python", "kubernetes", "redis", "postgres", "fastapi"],
    )
    # All evidence ids should collapse to one experience entry -> low diversity.
    assert critique.evidence_diversity < 0.5
    assert any("evidence_concentration" in f for f in critique.flags)


def test_verdict_consistency_score_in_critique():
    profile = _profile_polyglot()
    posting = _startup_posting()
    agent = PeerCritiqueAgent(TraceStore(), ThoughtBus())
    draft = build_resume_draft(profile, posting, keywords=["python", "ownership"])
    result = agent.run(
        PeerCritiqueInputs(draft=draft, posting=posting, document_id="d-1",
                          required_keywords=["python", "ownership"]),
        task_id="t-1",
    )
    # Self-critique should report verdict_consistency = 1.0.
    assert result.trace.self_critique.get("verdict_consistency", 0.0) == 1.0
