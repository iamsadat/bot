"""Personal mode: JobHunt as one person's tool rather than a product.

``python -m jobhunt me`` reads a gitignored ``me.env``, switches on every
background capability, and builds the profile from the owner's résumé file the
first time it starts. Nothing personal lives in the repository — it is public —
so everything about the owner comes from ``me.env`` and the résumé on disk.

Set ``JOBHUNT_PERSONAL=1`` to get the same behaviour from the ASGI entrypoint
(``jobhunt.dashboard.app``) that Docker and Render run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_TRUE = ("1", "true", "yes", "on")

# Applied with setdefault, so anything in me.env or the shell wins.
DEFAULTS: dict[str, str] = {
    # Sweep every 6 hours, and once shortly after start so a fresh launch is
    # not empty until the first interval elapses.
    "JOBHUNT_DISCOVERY_POLL_SECONDS": "21600",
    "JOBHUNT_DISCOVERY_FIRST_DELAY_SECONDS": "20",
    "JOBHUNT_DIGEST_INTERVAL_SECONDS": "86400",
    # One user can afford to tailor more and read every seeded board per sweep.
    "JOBHUNT_SHORTLIST_CAP": "25",
    "JOBHUNT_BOARDS_PER_PAGE": "0",
    "JOBHUNT_AUTOFILL_ENABLED": "1",
    "JOBHUNT_DEV_NAV": "0",
}


def is_personal() -> bool:
    return os.environ.get("JOBHUNT_PERSONAL", "").strip().lower() in _TRUE


def load_env_file(path: str | Path) -> list[str]:
    """Load ``KEY=VALUE`` lines into ``os.environ`` without overriding.

    Deliberately tiny instead of a python-dotenv dependency: comments, blank
    lines, an optional ``export`` prefix and matching quotes are all it needs.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return []
    loaded = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def enable(env_file: str | Path | None = "me.env") -> list[str]:
    """Turn on personal mode for this process. Returns the keys loaded."""
    loaded = load_env_file(env_file) if env_file else []
    os.environ["JOBHUNT_PERSONAL"] = "1"
    if os.environ.get("VERCEL"):
        # Frozen between requests: sweeps come from Vercel Cron instead, and
        # there is no browser to fill forms in.
        for key in ("JOBHUNT_DISCOVERY_POLL_SECONDS", "JOBHUNT_DIGEST_INTERVAL_SECONDS",
                    "JOBHUNT_AUTOFILL_ENABLED"):
            os.environ.setdefault(key, "0")
    for key, value in DEFAULTS.items():
        os.environ.setdefault(key, value)
    return loaded


def display_available() -> bool:
    """Whether a headed browser can open — the co-pilot needs a screen."""
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


from jobhunt.onboarding import contact_from_text  # noqa: E402,F401  (re-exported)


def _split(value: str) -> list[str]:
    return [s.strip() for s in value.split(",") if s.strip()]


def seed_profile(state, *, force: bool = False) -> str | None:
    """Build the owner's profile from ``JOBHUNT_ME_RESUME`` if none exists.

    Uses the same parse → build → merge path as the onboarding UI, so seeding
    cannot drift from what uploading the file would do. Edits made in the UI
    are never overwritten unless ``force`` (``jobhunt me --reseed``) is passed.
    Returns a one-line summary, or None when nothing was seeded.
    """
    if state.user_profile is not None and not force:
        return None
    path = os.environ.get("JOBHUNT_ME_RESUME", "").strip()
    if not path:
        return None
    file = Path(path).expanduser()
    if not file.is_file():
        return f"JOBHUNT_ME_RESUME not found: {file}"

    from jobhunt.dashboard.server import _apply_parsed_resume
    from jobhunt.onboarding import (
        build_user_profile,
        extract_resume_text,
        parse_resume_text,
    )

    text = extract_resume_text(file.name, file.read_bytes())
    parsed = parse_resume_text(text)
    contact = contact_from_text(text)
    env = os.environ.get
    form: dict[str, Any] = {
        "name": env("JOBHUNT_ME_NAME") or contact["name"] or "Me",
        "email": env("JOBHUNT_ME_EMAIL") or contact["email"],
        "phone": env("JOBHUNT_ME_PHONE") or contact["phone"],
        "target_roles": _split(env("JOBHUNT_ME_ROLES", "")),
        "locations": _split(env("JOBHUNT_ME_LOCATIONS", "")),
        # Co-pilot by default; autonomy is wired and one switch away.
        "auto_apply": env("JOBHUNT_ME_AUTO_APPLY", "").lower() in _TRUE,
        "daily_apply_cap": int(env("JOBHUNT_ME_DAILY_CAP") or 5),
        "relevance_floor": float(env("JOBHUNT_ME_RELEVANCE_FLOOR") or 0.75),
    }
    profile = build_user_profile(form)
    _apply_parsed_resume(profile, parsed)
    if state.user_profile is not None:  # --reseed keeps what the UI configured
        old = state.user_profile
        profile.application_answers = old.application_answers
        profile.veto_companies = old.veto_companies
    # Only answers the résumé actually evidences; the rest are set in the UI.
    answers = profile.application_answers
    if profile.experience_years is not None:
        answers.setdefault("years_experience", str(profile.experience_years))
    for key in ("linkedin", "github", "website"):
        if profile.links.get(key):
            answers.setdefault(key, profile.links[key])

    state.user_profile = profile
    state.last_resume_parse = parsed
    state.persist()
    return (f"Profile built from {file.name}: {profile.name}, "
            f"{', '.join(profile.target_roles) or 'no target roles'} · "
            f"{', '.join(profile.locations) or 'no locations'} · "
            f"{len(profile.skills)} skills · "
            f"{profile.experience_years if profile.experience_years is not None else '?'} yrs")
