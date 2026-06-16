"""Submitter registry — picks the right submitter for a given URL."""

from __future__ import annotations

from jobhunt.submitters.base import Submitter, SubmitResult


class SubmitterRegistry:
    """Holds a list of :class:`Submitter` instances and dispatches by URL.

    Usage::

        registry = SubmitterRegistry([GreenhouseSubmitter(poster), LeverSubmitter(poster)])
        result = registry.submit(plan_dict)
    """

    def __init__(self, submitters: list[Submitter] | None = None) -> None:
        self._submitters: list[Submitter] = list(submitters or [])

    def register(self, submitter: Submitter) -> None:
        """Add a submitter at runtime."""
        self._submitters.append(submitter)

    def for_url(self, url: str) -> Submitter | None:
        """Return the first submitter that supports *url*, or ``None``."""
        for s in self._submitters:
            if s.supports(url):
                return s
        return None

    def submit(self, plan: dict) -> SubmitResult | None:
        """Find a submitter for ``plan['url']`` and call it.

        Returns ``None`` if no submitter supports the URL.
        """
        url = plan.get("url", "")
        submitter = self.for_url(url)
        if submitter is None:
            return None
        return submitter.submit(plan)
