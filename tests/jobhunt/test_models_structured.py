"""Tests for the structured profile model + persistence round-trip."""

from __future__ import annotations

from dataclasses import asdict

from jobhunt.dashboard.persistence import _profile_from_dict
from jobhunt.models import Education, Experience, Project, UserProfile


def _profile() -> UserProfile:
    return UserProfile(
        user_id="u1", name="Ada", email="ada@x.com",
        target_roles=["backend"], locations=["Remote"],
        skills=["python"],
        experiences=[{"title": "Eng", "company": "Globex",
                      "start": "2019", "end": "2023", "bullets": ["b1", "b2"]}],
        education=[{"school": "MIT", "degree": "BSc"}],
        projects=[{"name": "JobHunt", "link": "gh/ada", "bullets": ["p1"]}],
        links={"github": "github.com/ada"},
        auto_apply=True, daily_apply_cap=7, relevance_floor=0.25,
    )


def test_round_trip_preserves_new_fields():
    p = _profile()
    restored = _profile_from_dict(asdict(p))
    assert restored.education[0]["school"] == "MIT"
    assert restored.projects[0]["name"] == "JobHunt"
    assert restored.links["github"] == "github.com/ada"
    assert restored.auto_apply is True
    assert restored.daily_apply_cap == 7
    assert restored.relevance_floor == 0.25


def test_legacy_dict_without_new_keys_still_loads():
    legacy = {
        "user_id": "u1", "name": "Ada", "email": "ada@x.com",
        "target_roles": ["backend"], "locations": ["Remote"],
        "skills": ["python"],
        "experiences": [{"title": "Eng", "company": "Globex"}],
    }
    p = _profile_from_dict(legacy)
    assert p.education == []
    assert p.projects == []
    assert p.links == {}
    assert p.auto_apply is False
    assert p.relevance_floor == 0.0


def test_structured_accessors_coerce_dicts_to_dataclasses():
    p = _profile()
    exps = p.structured_experiences()
    assert isinstance(exps[0], Experience)
    assert exps[0].title == "Eng"
    assert exps[0].bullets == ["b1", "b2"]
    edu = p.structured_education()
    assert isinstance(edu[0], Education)
    assert edu[0].school == "MIT"
    projs = p.structured_projects()
    assert isinstance(projs[0], Project)
    assert projs[0].name == "JobHunt"


def test_structured_accessor_ignores_unknown_keys():
    p = UserProfile(
        user_id="u", name="n", email="e@x.com",
        target_roles=[], locations=[],
        experiences=[{"title": "Eng", "bogus_key": "ignored", "company": "C"}],
    )
    exps = p.structured_experiences()
    assert exps[0].title == "Eng"
    assert exps[0].company == "C"
    assert not hasattr(exps[0], "bogus_key")
