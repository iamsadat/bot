"""Shared fixtures for jobhunt tests."""

from __future__ import annotations

import pytest

from jobhunt.adapters import FixtureSource
from jobhunt.models import UserProfile
from jobhunt.trace import ThoughtBus, TraceStore


@pytest.fixture
def profile() -> UserProfile:
    return UserProfile(
        user_id="u-test",
        name="Test User",
        email="test@example.com",
        target_roles=["backend engineer", "staff engineer"],
        locations=["Remote", "San Francisco"],
        min_salary=180_000,
        remote_ok=True,
        skills=[
            "python", "postgresql", "redis", "kubernetes", "fastapi",
            "distributed", "observability", "langgraph",
        ],
        experiences=[
            {"title": "Senior Backend Engineer",
             "highlight": "Python services on Kubernetes."},
        ],
        veto_companies=["Fabrikam"],
        weekly_target=10,
    )


@pytest.fixture
def store() -> TraceStore:
    return TraceStore()


@pytest.fixture
def bus() -> ThoughtBus:
    return ThoughtBus()


@pytest.fixture
def all_sources() -> list[FixtureSource]:
    return [
        FixtureSource(name="greenhouse",
                      only_sources=["greenhouse", "ashby", "lever"]),
        FixtureSource(name="linkedin", only_sources=["linkedin"]),
        FixtureSource(name="indeed", only_sources=["indeed"]),
        FixtureSource(name="company-rss", only_sources=["company-rss"]),
    ]
