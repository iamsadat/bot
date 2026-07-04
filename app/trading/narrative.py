"""Plain-English status strings for the engine's tick heartbeat.

Pure functions only — no I/O, no DB, no broker calls — so the engine loop in
``engine.py`` can stay focused on control flow.
"""

from __future__ import annotations

import datetime as dt


def iso(ts: dt.datetime | None) -> str | None:
    return ts.isoformat() if ts is not None else None


def clock_blob(state: str, clock) -> dict:
    return {
        "state": state,
        "next_open": iso(clock.next_open),
        "next_close": iso(clock.next_close),
    }


def for_scan(candidates: list[dict], regime: str) -> str:
    """One plain-English line summarizing what the last scan decided."""
    n = len(candidates)
    regime_label = (regime or "chop").upper()
    if not candidates:
        return f"Scanned {n} symbols · regime {regime_label} · no data"
    best = candidates[0]  # already sorted by rank_score desc by the caller
    sign = "+" if best["score"] >= 0 else ""
    if best["selected"]:
        verdict = "selected for entry"
    elif best["direction"] == 0:
        verdict = "no signal"
    elif not best["mtf"]:
        verdict = "failed multi-timeframe confirmation"
    elif best["news_blocked"]:
        verdict = "blocked by news filter"
    else:
        verdict = "below threshold — no trade"
    return (f"Scanned {n} symbols · regime {regime_label} · "
            f"best {best['symbol']} {sign}{best['score']:.2f} ({verdict})")


def for_closed(clock) -> str:
    when = clock.next_open.strftime("%a %H:%M UTC") if clock.next_open else "unknown"
    return f"Market closed · next open {when}"
