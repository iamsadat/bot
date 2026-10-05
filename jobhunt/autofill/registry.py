"""Autofill registry — picks the right :class:`Autofiller` for a URL.

Mirrors :class:`jobhunt.submitters.registry.SubmitterRegistry`'s dispatch
style, but since ATS career-site URLs are dispatched purely by domain
(rather than each autofiller declaring its own ``supports``), the
domain → autofiller mapping lives here.

Usage::

    registry = AutofillRegistry()
    result = registry.fill(page, url, profile, answers)
"""

from __future__ import annotations

import inspect
from typing import Any

from jobhunt.autofill.base import Autofiller, AutofillResult, Page
from jobhunt.autofill.generic import GenericAutofiller
from jobhunt.autofill.icims import IcimsAutofiller
from jobhunt.autofill.workday import WorkdayAutofiller

# Domain substring -> Autofiller factory. Order matters: first match wins.
_DEFAULT_ROUTES: tuple[tuple[str, type], ...] = (
    ("myworkdayjobs.com", WorkdayAutofiller),
    ("icims.com", IcimsAutofiller),
)


def _accepts_submit(fn: Any) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "submit" in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


class AutofillRegistry:
    """Dispatches to the right :class:`Autofiller` by inspecting a URL.

    Unrecognized domains fall back to :class:`GenericAutofiller`, so
    ``for_url`` always returns something usable (unlike
    ``SubmitterRegistry.for_url``, which may return ``None`` — autofill
    is the catch-all path for "manual"/non-API ATSes, so there's always
    a fallback strategy).
    """

    def __init__(
        self,
        autofillers: dict[str, Autofiller] | None = None,
        *,
        fallback: Autofiller | None = None,
    ) -> None:
        if autofillers is None:
            self._routes: list[tuple[str, Autofiller]] = [
                (domain, cls()) for domain, cls in _DEFAULT_ROUTES
            ]
        else:
            self._routes = list(autofillers.items())
        self._fallback: Autofiller = fallback or GenericAutofiller()

    def register(self, domain: str, autofiller: Autofiller) -> None:
        """Add/override a domain -> autofiller route at runtime."""
        self._routes.append((domain, autofiller))

    def for_url(self, url: str) -> Autofiller:
        """Return the autofiller that handles *url*.

        Always returns an :class:`Autofiller` — falls back to
        :class:`GenericAutofiller` when no domain matches.
        """
        lowered = url.lower()
        for domain, autofiller in self._routes:
            if domain in lowered:
                return autofiller
        return self._fallback

    def fill(
        self,
        page: Page,
        url: str,
        profile: Any,
        answers: dict[str, str],
        *,
        submit: bool = False,
    ) -> AutofillResult:
        """Find the right autofiller for *url* and run it against *page*.

        Action buttons are only clicked when ``submit=True``. An autofiller
        that doesn't take ``submit`` is called without it (and so never
        receives permission to click).
        """
        autofiller = self.for_url(url)
        if submit and _accepts_submit(autofiller.fill):
            return autofiller.fill(page, profile, answers, submit=True)  # type: ignore[call-arg]
        return autofiller.fill(page, profile, answers)
