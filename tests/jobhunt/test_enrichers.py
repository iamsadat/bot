"""Tests for jobhunt.enrichers and the enricher-aware VettingAgent.

Coverage goals (≥ 10 tests):
  1-4  Each heuristic: positive JD → high signal value
  5-8  Each heuristic: negative/empty JD → low/empty signal
  9    Empty jd_text → all heuristics return []
  10   all_heuristics() returns exactly 4 instances
  11   VettingAgent with enrichers: rationale includes enricher metrics
  12   VettingAgent with user-supplied weights: normalisation + freshness-boost
  13   LayoffsHeuristic inversion: high-risk posting scores lower
  14   VettingAgent without enrichers: identical to legacy baseline
  15   EnrichmentSignal fields are accessible and typed correctly
  16   GlassdoorHeuristic: culture_warning signal when negatives dominate
  17   CrunchbaseHeuristic: stage value mapped correctly from text
  18   NewsHeuristic: negative news terms reduce sentiment
  19   LayoffsHeuristic: risk rises with more risk phrases
  20   VettingAgent with weights=None and enrichers=[]: identical to baseline
"""

from __future__ import annotations

import time
import uuid

import pytest

from jobhunt.enrichers import (
    CrunchbaseHeuristic,
    EnrichmentSignal,
    Enricher,
    GlassdoorHeuristic,
    LayoffsHeuristic,
    NewsHeuristic,
    all_heuristics,
)
from jobhunt.models import (
    DiscoveryBatch,
    JobPosting,
    RiskRewardScorecard,
    UserProfile,
)
from jobhunt.agents.vetting import VettingAgent, VettingInputs
from jobhunt.trace import ThoughtBus, TraceStore


# ------------------------------------------------------------------ fixtures

def _posting(
    jd_text: str,
    company: str = "Acme",
    salary_min: int | None = 180_000,
    salary_max: int | None = 220_000,
    remote: bool = True,
    location: str = "Remote",
    posted_at: float | None = None,
) -> JobPosting:
    posted_at = posted_at if posted_at is not None else time.time() - 86400  # 1 day ago
    return JobPosting(
        job_id=uuid.uuid4().hex,
        source="test",
        source_id="s1",
        url="https://example.com/job",
        title="Software Engineer",
        company=company,
        location=location,
        jd_text=jd_text,
        posted_at=posted_at,
        salary_min=salary_min,
        salary_max=salary_max,
        remote=remote,
    )


def _profile() -> UserProfile:
    return UserProfile(
        user_id="u1",
        name="Alice",
        email="alice@example.com",
        target_roles=["Software Engineer"],
        locations=["San Francisco"],
        min_salary=160_000,
        remote_ok=True,
    )


def _batch(postings: list[JobPosting]) -> DiscoveryBatch:
    return DiscoveryBatch(
        batch_id=uuid.uuid4().hex,
        plan_id="p1",
        postings=postings,
        sources_used=["test"],
    )


def _agent() -> VettingAgent:
    return VettingAgent(trace_store=TraceStore(), bus=ThoughtBus())


# ==========================================================================
# 1. GlassdoorHeuristic: positive culture JD → rating close to 1.0
# ==========================================================================

def test_glassdoor_positive_culture():
    g = GlassdoorHeuristic()
    jd = (
        "We invest in growth and mentorship for all engineers. "
        "Remote-friendly with wellness stipends and generous equity. "
        "Flexible hours and a collaborative, inclusive environment."
    )
    signals = g.enrich(_posting(jd))
    rating_sig = next(s for s in signals if s.metric == "rating")
    assert rating_sig.value > 0.6, f"Expected high rating, got {rating_sig.value}"


# ==========================================================================
# 2. CrunchbaseHeuristic: Series B mention → stage ≥ 0.6
# ==========================================================================

def test_crunchbase_series_b_stage():
    c = CrunchbaseHeuristic()
    jd = "We completed our Series B and are now expanding the team rapidly."
    signals = c.enrich(_posting(jd))
    stage_sig = next(s for s in signals if s.metric == "stage")
    assert stage_sig.value >= 0.6, f"Expected stage ≥ 0.6 for Series B, got {stage_sig.value}"


# ==========================================================================
# 3. NewsHeuristic: positive terms → sentiment > 0.5
# ==========================================================================

def test_news_positive_sentiment():
    n = NewsHeuristic()
    jd = (
        "The company announced a record revenue milestone and launched a "
        "major partnership with a Fortune 500 firm. We raised $50M Series C."
    )
    signals = n.enrich(_posting(jd))
    sent_sig = next(s for s in signals if s.metric == "recent_news_sentiment")
    assert sent_sig.value > 0.5, f"Expected positive sentiment, got {sent_sig.value}"


# ==========================================================================
# 4. LayoffsHeuristic: no risk phrases → risk = 0.0
# ==========================================================================

def test_layoffs_no_risk_phrases():
    lay = LayoffsHeuristic()
    jd = "We are hiring! Great culture, free snacks, and competitive pay."
    signals = lay.enrich(_posting(jd))
    risk_sig = next(s for s in signals if s.metric == "recent_layoff_risk")
    assert risk_sig.value == 0.0, f"Expected 0.0 risk, got {risk_sig.value}"


# ==========================================================================
# 5. GlassdoorHeuristic: negative culture phrases → rating < 0.5
# ==========================================================================

def test_glassdoor_negative_culture():
    g = GlassdoorHeuristic()
    jd = (
        "We are a ground floor opportunity. Willing to work weekends is a must. "
        "We offer unlimited PTO and a fast-paced environment."
    )
    signals = g.enrich(_posting(jd))
    rating_sig = next(s for s in signals if s.metric == "rating")
    assert rating_sig.value < 0.5, f"Expected low rating, got {rating_sig.value}"


# ==========================================================================
# 6. CrunchbaseHeuristic: post-IPO mention → stage = 0.9
# ==========================================================================

def test_crunchbase_post_ipo():
    c = CrunchbaseHeuristic()
    jd = "We are a post-IPO company listed on NASDAQ with 5000+ employees."
    signals = c.enrich(_posting(jd))
    stage_sig = next(s for s in signals if s.metric == "stage")
    # post-ipo maps to 0.9; nasdaq also maps to 0.9; whichever matches first
    assert stage_sig.value == 0.9, f"Expected 0.9 for post-IPO, got {stage_sig.value}"


# ==========================================================================
# 7. NewsHeuristic: negative terms → sentiment < 0.5
# ==========================================================================

def test_news_negative_sentiment():
    n = NewsHeuristic()
    jd = (
        "The company is under regulatory investigation following a lawsuit "
        "and ongoing controversy over a recent settlement."
    )
    signals = n.enrich(_posting(jd))
    sent_sig = next(s for s in signals if s.metric == "recent_news_sentiment")
    assert sent_sig.value < 0.5, f"Expected negative sentiment, got {sent_sig.value}"


# ==========================================================================
# 8. LayoffsHeuristic: multiple risk phrases → high risk
# ==========================================================================

def test_layoffs_high_risk():
    lay = LayoffsHeuristic()
    jd = (
        "Following a restructure and rightsizing initiative, "
        "we reduced headcount significantly."
    )
    signals = lay.enrich(_posting(jd))
    risk_sig = next(s for s in signals if s.metric == "recent_layoff_risk")
    assert risk_sig.value >= 0.9, f"Expected high risk, got {risk_sig.value}"


# ==========================================================================
# 9. Empty jd_text → all four heuristics return []
# ==========================================================================

def test_empty_jd_text_returns_no_signals():
    p = _posting("")
    for enricher in all_heuristics():
        signals = enricher.enrich(p)
        assert signals == [], (
            f"{enricher.name} returned {signals!r} for empty jd_text"
        )


# ==========================================================================
# 10. all_heuristics() returns exactly 4 instances
# ==========================================================================

def test_all_heuristics_count():
    h = all_heuristics()
    assert len(h) == 4
    names = {e.name for e in h}
    assert "glassdoor_heuristic" in names
    assert "crunchbase_heuristic" in names
    assert "news_heuristic" in names
    assert "layoffs_heuristic" in names


# ==========================================================================
# 11. VettingAgent with enrichers: rationale includes enricher keys
# ==========================================================================

def test_vetting_agent_enricher_rationale_present():
    jd = "We offer growth, mentorship, and equity. Series B funded. Announced record revenue."
    posting = _posting(jd)
    profile = _profile()
    inputs = VettingInputs(
        profile=profile,
        batch=_batch([posting]),
        enrichers=all_heuristics(),
    )
    agent = _agent()
    result = agent.run(inputs, task_id="t1")
    card = result.output[0]
    # At least one enricher key should appear in rationale.
    enricher_keys = [k for k in card.rationale if "." in k]
    assert len(enricher_keys) > 0, f"No enricher keys in rationale: {list(card.rationale)}"
    # Specifically, layoffs metric should be inverted in rationale.
    layoff_key = "layoffs_heuristic.recent_layoff_risk"
    if layoff_key in card.rationale:
        assert "[inverted]" in card.rationale[layoff_key]


# ==========================================================================
# 12. User-supplied weights: freshness boosted 10× → fresh posting wins
# ==========================================================================

def test_user_weights_freshness_boost():
    """A posting with a very recent date should outscore one that is 45 days
    old when freshness is weighted 10× relative to other dimensions."""
    now = time.time()

    fresh_posting = _posting(
        "Some decent job description." * 5,
        company="FreshCo",
        posted_at=now - 86400,           # 1 day old → freshness 1.0
        salary_min=100_000,              # below profile min → lower comp score
    )
    stale_posting = _posting(
        "Some decent job description." * 5,
        company="StaleCo",
        posted_at=now - 45 * 86400,     # 45 days old → freshness 0.4
        salary_min=200_000,              # above profile min → comp score 1.0
    )
    profile = _profile()  # min_salary=160_000

    freshness_heavy_weights = {
        "compensation": 0.05,
        "freshness": 0.80,               # massive freshness boost
        "specificity": 0.05,
        "location_fit": 0.05,
        # (will be normalised to sum to 1.0 — already sums to 0.95, close enough)
    }
    inputs = VettingInputs(
        profile=profile,
        batch=_batch([fresh_posting, stale_posting]),
        weights=freshness_heavy_weights,
    )
    agent = _agent()
    result = agent.run(inputs, task_id="t2")
    scores = {c.company_id: c.score for c in result.output}
    assert scores["FreshCo"] > scores["StaleCo"], (
        f"FreshCo ({scores['FreshCo']}) should beat StaleCo ({scores['StaleCo']}) "
        f"with freshness-heavy weights"
    )


# ==========================================================================
# 13. LayoffsHeuristic inversion: high-risk → lower overall score
# ==========================================================================

def test_layoff_risk_inversion_lowers_score():
    """A company with explicit layoff language should score lower than an
    otherwise identical posting without it, when LayoffsHeuristic is active."""
    base_jd = "We are hiring a software engineer. " * 20  # same length
    risky_jd = base_jd + " Following a restructure and rightsizing of headcount."

    safe_posting = _posting(base_jd, company="SafeCo")
    risky_posting = _posting(risky_jd, company="RiskyCo")

    profile = _profile()
    inputs = VettingInputs(
        profile=profile,
        batch=_batch([safe_posting, risky_posting]),
        enrichers=[LayoffsHeuristic()],
    )
    agent = _agent()
    result = agent.run(inputs, task_id="t3")
    scores = {c.company_id: c.score for c in result.output}
    assert scores["SafeCo"] >= scores["RiskyCo"], (
        f"SafeCo ({scores['SafeCo']}) should score >= RiskyCo ({scores['RiskyCo']})"
    )


# ==========================================================================
# 14. VettingAgent without enrichers: identical to legacy baseline
# ==========================================================================

def test_vetting_agent_no_enrichers_baseline():
    """Score without enrichers must equal the hand-computed legacy formula."""
    now = time.time()
    posted_at = now - 7 * 86400  # 7 days ago → freshness 1.0
    jd = "x" * 500               # len ≥ 400 → specificity 1.0
    posting = _posting(
        jd,
        company="Legacy",
        salary_min=200_000,   # ≥ profile min (160k) → comp 1.0
        remote=True,          # remote_ok=True → loc 1.0
        posted_at=posted_at,
    )
    profile = _profile()

    inputs = VettingInputs(
        profile=profile,
        batch=_batch([posting]),
        # NO enrichers, NO weights → pure legacy path
    )
    agent = _agent()
    result = agent.run(inputs, task_id="t4")
    card = result.output[0]

    # Legacy formula: 0.35*1.0 + 0.20*1.0 + 0.25*1.0 + 0.20*1.0 = 1.0
    expected = round(0.35 * 1.0 + 0.20 * 1.0 + 0.25 * 1.0 + 0.20 * 1.0, 3)
    assert card.score == expected, f"Expected {expected}, got {card.score}"


# ==========================================================================
# 15. EnrichmentSignal: fields accessible and typed correctly
# ==========================================================================

def test_enrichment_signal_fields():
    sig = EnrichmentSignal(
        enricher="test_enricher",
        company="Acme",
        metric="rating",
        value=0.75,
        detail="some detail",
    )
    assert sig.enricher == "test_enricher"
    assert sig.company == "Acme"
    assert sig.metric == "rating"
    assert sig.value == 0.75
    assert sig.detail == "some detail"


def test_enrichment_signal_default_detail():
    sig = EnrichmentSignal(enricher="x", company="Y", metric="m", value=0.5)
    assert sig.detail == ""


# ==========================================================================
# 16. GlassdoorHeuristic: culture_warning emitted when negatives dominate
# ==========================================================================

def test_glassdoor_culture_warning_emitted():
    g = GlassdoorHeuristic()
    # Three unambiguous negatives, zero positives.
    jd = (
        "You must be willing to work weekends and this is a ground floor "
        "opportunity. We offer unlimited pto."
    )
    signals = g.enrich(_posting(jd))
    metrics = {s.metric for s in signals}
    assert "culture_warning" in metrics, (
        f"Expected culture_warning signal; got metrics: {metrics}"
    )
    warn = next(s for s in signals if s.metric == "culture_warning")
    assert warn.value > 0.0


# ==========================================================================
# 17. CrunchbaseHeuristic: stage value correctly mapped
# ==========================================================================

def test_crunchbase_stage_mapping():
    c = CrunchbaseHeuristic()
    cases = [
        ("We completed a Series A round.", 0.5),
        ("We are Series C backed.", 0.7),
        ("Seed-stage startup looking for founders.", 0.4),
    ]
    for jd, expected_stage in cases:
        signals = c.enrich(_posting(jd))
        stage_sig = next(s for s in signals if s.metric == "stage")
        assert stage_sig.value == expected_stage, (
            f"JD {jd!r}: expected stage {expected_stage}, got {stage_sig.value}"
        )


# ==========================================================================
# 18. NewsHeuristic: negative terms reduce sentiment below neutral
# ==========================================================================

def test_news_neutral_baseline():
    n = NewsHeuristic()
    jd = "We are building great products for our customers."
    signals = n.enrich(_posting(jd))
    sent = next(s for s in signals if s.metric == "recent_news_sentiment")
    # No positive or negative terms → exactly neutral 0.5
    assert sent.value == 0.5, f"Expected 0.5 neutral, got {sent.value}"


def test_news_investigation_reduces_sentiment():
    n = NewsHeuristic()
    jd = (
        "The company is under investigation and faces a regulatory fine "
        "following a controversial settlement."
    )
    signals = n.enrich(_posting(jd))
    sent = next(s for s in signals if s.metric == "recent_news_sentiment")
    assert sent.value < 0.5


# ==========================================================================
# 19. LayoffsHeuristic: risk increases with more risk phrases
# ==========================================================================

def test_layoffs_risk_scales_with_phrases():
    lay = LayoffsHeuristic()
    low_jd = "We are restructuring our product roadmap slightly."
    high_jd = (
        "We are restructuring the org after a significant rightsizing of headcount "
        "and a workforce reduction plan."
    )
    low_signals = lay.enrich(_posting(low_jd))
    high_signals = lay.enrich(_posting(high_jd))
    low_risk = next(s for s in low_signals if s.metric == "recent_layoff_risk").value
    high_risk = next(s for s in high_signals if s.metric == "recent_layoff_risk").value
    assert high_risk > low_risk, (
        f"Expected high_risk ({high_risk}) > low_risk ({low_risk})"
    )


# ==========================================================================
# 20. VettingAgent with weights=None and enrichers=[] → identical to baseline
# ==========================================================================

def test_vetting_agent_explicit_empty_identical_to_baseline():
    """Explicitly passing weights=None and enrichers=[] should yield the same
    score as the default constructor."""
    now = time.time()
    posted_at = now - 3 * 86400
    jd = "y" * 300  # len ≥ 200 → specificity 0.7
    posting = _posting(
        jd,
        company="Co1",
        salary_min=170_000,
        remote=True,
        posted_at=posted_at,
    )
    profile = _profile()

    inputs_default = VettingInputs(profile=profile, batch=_batch([posting]))
    inputs_explicit = VettingInputs(
        profile=profile,
        batch=_batch([posting]),
        weights=None,
        enrichers=[],
    )
    agent = _agent()
    r1 = agent.run(inputs_default, task_id="ta")
    r2 = agent.run(inputs_explicit, task_id="tb")
    assert r1.output[0].score == r2.output[0].score


# ==========================================================================
# 21. Enricher protocol: GlassdoorHeuristic satisfies Enricher
# ==========================================================================

def test_enricher_protocol_satisfied():
    assert isinstance(GlassdoorHeuristic(), Enricher)
    assert isinstance(CrunchbaseHeuristic(), Enricher)
    assert isinstance(NewsHeuristic(), Enricher)
    assert isinstance(LayoffsHeuristic(), Enricher)
