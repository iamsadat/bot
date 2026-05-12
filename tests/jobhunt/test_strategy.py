from jobhunt.agents.strategy import StrategyAgent, StrategyInputs


def test_strategy_produces_full_plan_graph(profile, store, bus):
    agent = StrategyAgent(store, bus)
    res = agent.run(StrategyInputs(profile=profile), task_id="t")
    plan = res.output
    assert plan is not None
    agents = [s.agent for s in plan.steps]
    assert agents == ["discovery", "vetting", "resume", "submission", "tracking"]
    # Discovery step has one query per (role, location) pair.
    disc = plan.steps[0]
    assert len(disc.inputs["queries"]) == len(profile.target_roles) * len(profile.locations)
    # Dependencies form a chain (each step depends on the previous).
    for i in range(1, len(plan.steps)):
        assert plan.steps[i].depends_on == [plan.steps[i - 1].step_id]


def test_strategy_self_critique_above_threshold(profile, store, bus):
    agent = StrategyAgent(store, bus)
    res = agent.run(StrategyInputs(profile=profile), task_id="t")
    assert res.trace.confidence >= agent.quality_threshold
