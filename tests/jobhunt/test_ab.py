"""Tests for jobhunt.ab — A/B experiment framework."""

from __future__ import annotations

import pytest

from jobhunt.ab import Experiment, ExperimentRegistry, Variant
from jobhunt.agents.improvement import ImprovementAgent, ImprovementInputs
from jobhunt.models import JobHuntPlan
from jobhunt.trace import ThoughtBus, TraceStore


# ===================================================================== helpers


def _make_experiment(
    name: str = "test_exp",
    target: str = "discovery",
    rollback_threshold: float = 0.5,
    min_impressions: int = 10,
) -> Experiment:
    return Experiment(
        name=name,
        target=target,
        variants=[
            Variant("control", {"min_relevance": 0.5}),
            Variant("lower", {"min_relevance": 0.4}),
        ],
        rollback_threshold=rollback_threshold,
        min_impressions=min_impressions,
    )


def _fill_variant(variant: Variant, impressions: int, successes: int) -> None:
    variant.impressions = impressions
    variant.successes = successes


# =================================================================== Variant


def test_variant_success_rate_math() -> None:
    v = Variant("control", {}, impressions=20, successes=15)
    assert v.success_rate == pytest.approx(0.75)


def test_variant_success_rate_zero_impressions() -> None:
    v = Variant("control", {})
    assert v.success_rate == 0.0


def test_variant_success_rate_all_successes() -> None:
    v = Variant("control", {}, impressions=10, successes=10)
    assert v.success_rate == 1.0


# ================================================================= Experiment


def test_experiment_assign_is_deterministic() -> None:
    exp = _make_experiment()
    key = "user-abc-123"
    first = exp.assign(key)
    second = exp.assign(key)
    assert first.name == second.name


def test_experiment_assign_distributes_across_variants() -> None:
    """Over 1 000 random-ish keys, each variant should get 40–60% of traffic."""
    exp = _make_experiment()
    counts: dict[str, int] = {"control": 0, "lower": 0}
    for i in range(1000):
        v = exp.assign(f"user-{i}")
        counts[v.name] += 1
    total = sum(counts.values())
    for name, count in counts.items():
        fraction = count / total
        assert 0.40 <= fraction <= 0.60, (
            f"Variant '{name}' got {fraction:.1%} of traffic — expected 40-60%"
        )


def test_experiment_record_updates_counters() -> None:
    exp = _make_experiment()
    exp.record("control", success=True)
    exp.record("control", success=False)
    exp.record("lower", success=True)

    ctrl = exp.control()
    assert ctrl.impressions == 2
    assert ctrl.successes == 1

    lower = exp.variants[1]
    assert lower.impressions == 1
    assert lower.successes == 1


def test_experiment_record_unknown_variant_raises() -> None:
    exp = _make_experiment()
    with pytest.raises(ValueError, match="Unknown variant"):
        exp.record("nonexistent", success=True)


def test_experiment_winner_none_below_significance() -> None:
    exp = _make_experiment(min_impressions=10)
    # Control has enough; treatment does not yet
    _fill_variant(exp.variants[0], impressions=15, successes=10)
    _fill_variant(exp.variants[1], impressions=5, successes=4)  # < min_impressions
    assert exp.winner() is None


def test_experiment_winner_none_when_gap_is_small() -> None:
    exp = _make_experiment(min_impressions=10)
    _fill_variant(exp.variants[0], impressions=20, successes=10)  # 50%
    _fill_variant(exp.variants[1], impressions=20, successes=11)  # 55% — gap < 10%
    assert exp.winner() is None


def test_experiment_winner_returns_better_variant() -> None:
    exp = _make_experiment(min_impressions=10)
    _fill_variant(exp.variants[0], impressions=20, successes=10)   # 50%
    _fill_variant(exp.variants[1], impressions=20, successes=13)   # 65% — gap ≥ 10%
    w = exp.winner()
    assert w is not None
    assert w.name == "lower"


def test_experiment_should_rollback_true_when_variant_is_bad() -> None:
    exp = _make_experiment(rollback_threshold=0.5, min_impressions=10)
    # Control rate = 80%; treatment rate = 20% → 20% < 50% of 80% = 40%
    _fill_variant(exp.variants[0], impressions=20, successes=16)  # 80%
    _fill_variant(exp.variants[1], impressions=20, successes=4)   # 20%
    assert exp.should_rollback() is True


def test_experiment_should_rollback_false_within_tolerance() -> None:
    exp = _make_experiment(rollback_threshold=0.5, min_impressions=10)
    # Control rate = 80%; treatment rate = 60% → 60% >= 50% * 80% = 40%
    _fill_variant(exp.variants[0], impressions=20, successes=16)  # 80%
    _fill_variant(exp.variants[1], impressions=20, successes=12)  # 60%
    assert exp.should_rollback() is False


def test_experiment_should_rollback_false_below_min_impressions() -> None:
    exp = _make_experiment(rollback_threshold=0.5, min_impressions=10)
    _fill_variant(exp.variants[0], impressions=15, successes=12)
    _fill_variant(exp.variants[1], impressions=5, successes=0)   # < min_impressions
    assert exp.should_rollback() is False


# ============================================================ ExperimentRegistry


def test_registry_register_and_get() -> None:
    reg = ExperimentRegistry()
    exp = _make_experiment(name="exp1")
    reg.register(exp)
    assert reg.get("exp1") is exp
    assert reg.get("nonexistent") is None


def test_registry_all_returns_all() -> None:
    reg = ExperimentRegistry()
    reg.register(_make_experiment(name="a"))
    reg.register(_make_experiment(name="b"))
    names = {e.name for e in reg.all()}
    assert names == {"a", "b"}


def test_registry_to_dict_from_dict_roundtrip() -> None:
    reg = ExperimentRegistry()
    exp = _make_experiment(name="rt_exp", min_impressions=5)
    _fill_variant(exp.variants[0], impressions=10, successes=7)
    _fill_variant(exp.variants[1], impressions=12, successes=5)
    reg.register(exp)

    d = reg.to_dict()
    reg2 = ExperimentRegistry.from_dict(d)

    exp2 = reg2.get("rt_exp")
    assert exp2 is not None
    assert exp2.name == "rt_exp"
    assert exp2.min_impressions == 5
    assert exp2.variants[0].impressions == 10
    assert exp2.variants[1].successes == 5


# ============================================================ ImprovementAgent integration


def _make_plan() -> JobHuntPlan:
    return JobHuntPlan(
        plan_id="plan-001",
        user_id="u-test",
        milestones=["find jobs"],
        steps=[],
        version=2,
    )


def test_improvement_agent_without_experiments_unchanged() -> None:
    """Existing behaviour: no experiments registry → no AB-related suggestions."""
    store = TraceStore()
    bus = ThoughtBus()
    agent = ImprovementAgent(store, bus)
    inputs = ImprovementInputs(plan=_make_plan(), results={})
    result = agent.run(inputs, task_id="t1")
    assert result.output is not None
    changes = [s.get("change", "") for s in result.output.suggestions]
    assert not any("rollback" in c or "promote_winner" in c for c in changes)


def test_improvement_agent_surfaces_promote_winner() -> None:
    """When an experiment has a winner, agent emits a promote_winner suggestion."""
    store = TraceStore()
    bus = ThoughtBus()
    agent = ImprovementAgent(store, bus)

    reg = ExperimentRegistry()
    exp = _make_experiment(name="disc_thresh", min_impressions=10)
    # Make treatment a clear winner: control=50%, lower=70%
    _fill_variant(exp.variants[0], impressions=20, successes=10)
    _fill_variant(exp.variants[1], impressions=20, successes=14)
    reg.register(exp)

    inputs = ImprovementInputs(plan=_make_plan(), results={}, experiments=reg)
    result = agent.run(inputs, task_id="t2")
    assert result.output is not None
    changes = [s.get("change", "") for s in result.output.suggestions]
    assert any("promote_winner" in c for c in changes)


def test_improvement_agent_surfaces_rollback_suggestion() -> None:
    """When an experiment should roll back, agent emits a rollback suggestion."""
    store = TraceStore()
    bus = ThoughtBus()
    agent = ImprovementAgent(store, bus)

    reg = ExperimentRegistry()
    exp = _make_experiment(name="bad_exp", rollback_threshold=0.5, min_impressions=10)
    # Control=80%, treatment=20%: 20% < 50%*80% = 40%
    _fill_variant(exp.variants[0], impressions=20, successes=16)
    _fill_variant(exp.variants[1], impressions=20, successes=4)
    reg.register(exp)

    inputs = ImprovementInputs(plan=_make_plan(), results={}, experiments=reg)
    result = agent.run(inputs, task_id="t3")
    assert result.output is not None
    changes = [s.get("change", "") for s in result.output.suggestions]
    assert any("rollback" in c for c in changes)


def test_improvement_agent_cycle_record_populated() -> None:
    """ImprovementOutput.cycle_record must contain cycle_id and plan_version."""
    store = TraceStore()
    bus = ThoughtBus()
    agent = ImprovementAgent(store, bus)
    plan = _make_plan()
    inputs = ImprovementInputs(plan=plan, results={})
    result = agent.run(inputs, task_id="t4")
    assert result.output is not None
    cr = result.output.cycle_record
    assert cr["cycle_id"] == plan.plan_id
    assert cr["plan_version"] == plan.version
    assert "suggestions" in cr
