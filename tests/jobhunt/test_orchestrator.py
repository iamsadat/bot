from jobhunt.adapters import FixtureSource
from jobhunt.agents import Orchestrator
from jobhunt.agents.orchestrator import OrchestratorInputs


def test_orchestrator_runs_full_cycle(profile, store, bus, all_sources):
    orch = Orchestrator(store, bus)
    res = orch.run(
        OrchestratorInputs(profile=profile, sources=all_sources),
        task_id="t",
    )
    out = res.output
    assert out is not None
    # All planned steps reached "done".
    statuses = [s.status for s in out.plan.steps]
    assert statuses == ["done"] * len(out.plan.steps)
    # All stages produced output.
    for k in ("discovery", "vetting", "resume", "submission", "tracking"):
        assert k in out.results
    # Reasoning traces from every agent landed in the immutable store.
    agents = {t.agent for t in store.all()}
    expected = {"strategy", "discovery", "vetting", "resume",
                "submission", "tracking", "improvement", "orchestrator"}
    assert expected.issubset(agents)


def test_orchestrator_replans_on_empty_discovery(profile, store, bus):
    """If the first pass returns 0 postings, the orchestrator must
    widen the discovery query (drop location) and try again."""

    # A fixture that contains a Remote posting, but the strategy will
    # initially constrain by 'San Francisco' which won't match it without
    # the remote-ok exception. Provide a profile with remote_ok=False to
    # force the first pass to drop it, then verify the orchestrator
    # widens the query and rediscovers.
    profile.remote_ok = False
    profile.locations = ["NowhereCity"]  # no fixture posting matches
    profile.target_roles = ["backend engineer"]

    orch = Orchestrator(
        store, bus
    )
    res = orch.run(
        OrchestratorInputs(
            profile=profile,
            sources=[FixtureSource(name="greenhouse",
                                   only_sources=["greenhouse"])],
        ),
        task_id="t",
    )
    out = res.output
    assert out is not None
    # Plan version was bumped due to re-plan.
    assert out.plan.version >= 1
    # Final discovery yielded postings (after dropping location).
    assert out.results["discovery"].postings
