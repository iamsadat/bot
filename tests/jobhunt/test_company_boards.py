"""The seeded company-board list, and how sweeps page through it.

Nothing here touches the network. These are properties of the seed list itself
and of ``page()`` / ``total_pages()`` / ``has_next()``, including the
``JOBHUNT_BOARDS_PER_PAGE`` override a personal deployment uses to read every
board on every sweep.
"""

from __future__ import annotations

import logging
import re

import pytest

from jobhunt import company_boards as cb

ENV = "JOBHUNT_BOARDS_PER_PAGE"


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    """Start from the default page size, whatever the shell running pytest sets."""
    monkeypatch.delenv(ENV, raising=False)


def _seeded() -> dict[str, tuple[str, ...]]:
    return {"greenhouse": cb.GREENHOUSE, "lever": cb.LEVER, "ashby": cb.ASHBY}


def _one_cycle(per_page: int | None = None) -> dict[str, list[str]]:
    """Every slug each kind reads over one full cycle of sweeps, in order."""
    seen: dict[str, list[str]] = {kind: [] for kind in _seeded()}
    for number in range(1, cb.total_pages(per_page) + 1):
        for kind, slugs in cb.page(number, per_page).items():
            seen[kind].extend(slugs)
    return seen


# --------------------------------------------------------------------------- #
# The list itself
# --------------------------------------------------------------------------- #

def test_no_company_is_seeded_twice_even_across_platforms():
    every = [s.lower() for group in _seeded().values() for s in group]
    assert len(every) == len(set(every))


def test_slugs_are_bare_tokens_not_urls_or_typos():
    for kind, group in _seeded().items():
        for slug in group:
            assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", slug), (kind, slug)


@pytest.mark.parametrize("slug", [
    # Retired: the board 404s.
    "phonepe", "postman",
    # Live, but no India-located role at all.
    "affirm", "instacart", "ramp", "sierra",
    # Aggregators re-posting other companies' jobs — not first-party postings.
    "jobgether", "weekdayworks", "smart-working-solutions",
    # A second board for a company already seeded; its postings overlap.
    "yipitdatajobs",
])
def test_known_bad_boards_stay_out(slug):
    assert slug not in {s.lower() for group in _seeded().values() for s in group}


# --------------------------------------------------------------------------- #
# Default paging
# --------------------------------------------------------------------------- #

def test_default_sweep_reads_a_slice():
    assert cb.boards_per_page() == cb.BOARDS_PER_PAGE
    first = cb.page(1)
    assert first["greenhouse"] == list(cb.GREENHOUSE[:cb.BOARDS_PER_PAGE])
    assert len(first["lever"]) == len(first["ashby"]) == cb.BOARDS_PER_PAGE // 3
    assert cb.has_next(1) is True


def test_default_cycle_reads_each_greenhouse_board_exactly_once_in_order():
    """Greenhouse sets the cycle at the default size, so no board is read twice
    before every other one has been read once."""
    assert _one_cycle()["greenhouse"] == list(cb.GREENHOUSE)


@pytest.mark.parametrize("per_page", [1, 2, 3, 4, 5, 6, 7, 10, 25, 60, 500, cb.ALL_BOARDS])
def test_one_cycle_reads_every_board_then_repeats_cleanly(per_page):
    """At any page size — including the tiny ones where Lever and Ashby get one
    board a sweep and outlast Greenhouse — one cycle reaches every board, the
    last page reports no next one, and the sweep after it is the first again."""
    seen = _one_cycle(per_page)
    for kind, group in _seeded().items():
        assert set(seen[kind]) == set(group), kind
    last = cb.total_pages(per_page)
    assert cb.has_next(last, per_page) is False
    if last > 1:
        assert cb.has_next(last - 1, per_page) is True
    assert cb.page(last + 1, per_page) == cb.page(1, per_page)


# --------------------------------------------------------------------------- #
# JOBHUNT_BOARDS_PER_PAGE
# --------------------------------------------------------------------------- #

def test_env_sets_the_page_size(monkeypatch):
    monkeypatch.setenv(ENV, "12")
    assert cb.boards_per_page() == 12
    first = cb.page(1)
    assert first["greenhouse"] == list(cb.GREENHOUSE[:12])
    assert first["lever"] == list(cb.LEVER[:4])
    assert first["ashby"] == list(cb.ASHBY[:4])
    assert cb.page(2)["greenhouse"] == list(cb.GREENHOUSE[12:24])
    assert cb.total_pages() == cb.total_pages(12) < cb.total_pages(cb.BOARDS_PER_PAGE)


@pytest.mark.parametrize("value", ["0", "all", "ALL", " All "])
def test_env_all_reads_every_board_on_every_sweep(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    everything = {kind: list(group) for kind, group in _seeded().items()}
    for number in (1, 2, 7):
        assert cb.page(number) == everything
    assert cb.total_pages() == 1
    # Nothing is left for "Fetch more" to reach: the one page was everything.
    assert cb.has_next(1) is False


@pytest.mark.parametrize("value", ["six", "-3", "4.5"])
def test_unusable_env_value_falls_back_to_the_default_with_a_warning(
    monkeypatch, caplog, value,
):
    monkeypatch.setenv(ENV, value)
    with caplog.at_level(logging.WARNING, logger=cb.__name__):
        assert cb.boards_per_page() == cb.BOARDS_PER_PAGE
    assert ENV in caplog.text
    assert cb.page(1) == cb.page(1, cb.BOARDS_PER_PAGE)


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_env_value_is_the_default(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    assert cb.boards_per_page() == cb.BOARDS_PER_PAGE


def test_explicit_page_size_beats_the_env(monkeypatch):
    monkeypatch.setenv(ENV, "all")
    assert cb.page(1, 6)["greenhouse"] == list(cb.GREENHOUSE[:6])
    assert cb.has_next(1, 6) is True
    monkeypatch.setenv(ENV, "6")
    assert cb.page(1, cb.ALL_BOARDS)["greenhouse"] == list(cb.GREENHOUSE)
