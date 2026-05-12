"""Entry point: ``python -m jobhunt <command>``.

Commands:

* ``demo``  — run a full plan→discover→vet→tailor→submit cycle. By
  default the demo runs offline against fixture sources; pass
  ``--live-ats`` to hit the real Greenhouse / Lever / Ashby public APIs
  for one example company per source.
* ``serve`` — start the FastAPI dashboard (requires ``fastapi`` and
  ``uvicorn``).
"""

from __future__ import annotations

import argparse
import json
import sys

from jobhunt.adapters import (
    AshbySource,
    FixtureSource,
    GreenhouseSource,
    LeverSource,
)
from jobhunt.agents import Orchestrator
from jobhunt.agents.orchestrator import OrchestratorInputs
from jobhunt.dashboard.server import DashboardState
from jobhunt.models import UserProfile
from jobhunt.trace import ThoughtBus, TraceStore


def _demo_profile() -> UserProfile:
    return UserProfile(
        user_id="u-demo",
        name="Ada Lovelace",
        email="ada@example.com",
        target_roles=["backend engineer", "staff engineer"],
        locations=["Remote", "San Francisco"],
        min_salary=180_000,
        remote_ok=True,
        skills=[
            "python", "postgresql", "redis", "kubernetes", "fastapi",
            "distributed", "observability", "langgraph",
        ],
        experiences=[
            {"title": "Senior Backend Engineer", "company": "Globex",
             "highlight": "Built distributed Python services on Kubernetes with Redis and pgvector."},
            {"title": "Staff Engineer", "company": "Initech",
             "highlight": "Led platform team; introduced OpenTelemetry and observability."},
        ],
        veto_companies=["Fabrikam"],
        weekly_target=10,
    )


def _print_demo(state: DashboardState, output) -> None:
    print("\n=== PLAN ===")
    print(json.dumps(
        {"milestones": output.plan.milestones,
         "steps": [(s.agent, s.action, s.status) for s in output.plan.steps]},
        indent=2,
    ))

    batch = output.results.get("discovery")
    if batch:
        print(f"\n=== DISCOVERY ({len(batch.postings)} postings) ===")
        for p in batch.postings[:10]:
            print(f"  [{p.relevance_score:.2f}] {p.title} @ {p.company}"
                  f" ({p.location}) — {p.source}")

    cards = output.results.get("vetting", [])
    if cards:
        print(f"\n=== VETTING ({sum(1 for c in cards if c.pass_threshold)}/{len(cards)} passed) ===")
        for c in cards:
            mark = "PASS" if c.pass_threshold else "FAIL"
            print(f"  [{c.score:.2f}] {mark} {c.company_id}")

    docs = output.results.get("resume", [])
    if docs:
        print(f"\n=== RESUME ({len(docs)} tailored) ===")
        for d in docs:
            print(f"  {d.company} :: {d.title} — coverage={d.keyword_coverage:.2f}"
                  f", missing={d.missing_keywords[:3]}")

    subs = output.results.get("submission", [])
    if subs:
        print(f"\n=== SUBMISSION ({len(subs)} packages) ===")
        for s in subs:
            print(f"  {s.company} → {s.route} (requires_user_click={s.requires_user_click})")

    print("\n=== TRACES ===")
    for t in state.trace_store.all():
        print(f"  [{t.agent}] confidence={t.confidence:.2f} — {t.decision}")


def _build_sources(live_ats: bool, greenhouse: list[str], lever: list[str],
                   ashby: list[str]):
    if live_ats:
        sources = []
        if greenhouse:
            sources.append(GreenhouseSource(board_tokens=greenhouse))
        if lever:
            sources.append(LeverSource(companies=lever))
        if ashby:
            sources.append(AshbySource(companies=ashby))
        if not sources:
            print("--live-ats requires at least one of --greenhouse / "
                  "--lever / --ashby", file=sys.stderr)
            raise SystemExit(2)
        return sources
    return [
        FixtureSource(name="greenhouse",
                      only_sources=["greenhouse", "ashby", "lever"]),
        FixtureSource(name="linkedin", only_sources=["linkedin"]),
        FixtureSource(name="indeed", only_sources=["indeed"]),
        FixtureSource(name="company-rss", only_sources=["company-rss"]),
    ]


def cmd_demo(args) -> int:
    store = TraceStore()
    bus = ThoughtBus()
    sources = _build_sources(
        live_ats=args.live_ats,
        greenhouse=args.greenhouse or [],
        lever=args.lever or [],
        ashby=args.ashby or [],
    )
    orch = Orchestrator(store, bus)
    profile = _demo_profile()
    result = orch.run(
        OrchestratorInputs(profile=profile, sources=sources),
        task_id="demo-task",
    )
    assert result.output is not None
    state = DashboardState(trace_store=store, bus=bus)
    state.plan = result.output.plan
    _print_demo(state, result.output)
    return 0


def cmd_serve(args) -> int:
    try:
        import uvicorn
    except ImportError:
        print("uvicorn not installed; run `pip install fastapi uvicorn`",
              file=sys.stderr)
        return 1

    from jobhunt.dashboard.persistence import DashboardStore

    trace = TraceStore()
    bus = ThoughtBus()
    db = DashboardStore(args.db_path)
    state = DashboardState(trace_store=trace, bus=bus, store=db)
    state.restore()
    if state.user_profile:
        print(f"  Restored profile: {state.user_profile.name} "
              f"({len(state.jobs)} jobs, hunt_status={state.hunt_status})")
    app = __import__(
        "jobhunt.dashboard.server", fromlist=["create_app"]
    ).create_app(state)
    print(f"\n  JobHunt dashboard running at http://{args.host}:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobhunt")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_demo = sub.add_parser("demo", help="run a full cycle")
    p_demo.add_argument("--live-ats", action="store_true",
                        help="hit real Greenhouse/Lever/Ashby APIs")
    p_demo.add_argument("--greenhouse", action="append",
                        metavar="BOARD_TOKEN",
                        help="Greenhouse board token (repeatable)")
    p_demo.add_argument("--lever", action="append", metavar="COMPANY",
                        help="Lever company slug (repeatable)")
    p_demo.add_argument("--ashby", action="append", metavar="COMPANY",
                        help="Ashby company slug (repeatable)")
    p_serve = sub.add_parser("serve", help="run the dashboard")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", default=8765, type=int)
    p_serve.add_argument("--db-path", default="jobhunt.db",
                         help="SQLite path for dashboard persistence")
    args = parser.parse_args(argv)
    if args.cmd == "demo":
        return cmd_demo(args)
    if args.cmd == "serve":
        return cmd_serve(args)
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
