"""Base protocols and utilities for ATS autofill.

Defines a minimal browser-page surface (``Page`` Protocol) so autofillers
can drive a real Playwright page in production while tests inject a
``FakePage`` that records every action deterministically — no real
browser, no network, fully offline.

This mirrors ``jobhunt.submitters.base``'s Poster/Submitter split: a tiny
Protocol for the "transport" (here, page automation) plus an
``Autofiller`` Protocol the registry can dispatch against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class AutofillError(Exception):
    """Raised when an autofiller cannot act on the page as expected.

    Typical cause: a driver action (fill/click/select/...) targets a
    selector that does not exist on the page.
    """


# -------------------------------------------------------------------- page

@runtime_checkable
class Page(Protocol):
    """Minimal browser-page surface an autofiller needs.

    Kept intentionally small and Playwright-compatible in spirit so a
    real ``PlaywrightPage`` adapter can implement this Protocol with thin
    wrappers around ``playwright.sync_api.Page`` methods of the same
    name. Tests inject ``FakePage`` instead.
    """

    def goto(self, url: str) -> None:
        """Navigate the page to *url*."""
        ...

    def fill(self, selector: str, value: str) -> None:
        """Type *value* into the text/textarea input matched by *selector*."""
        ...

    def select_option(self, selector: str, value: str) -> None:
        """Choose *value* in the ``<select>`` matched by *selector*."""
        ...

    def check(self, selector: str) -> None:
        """Check the checkbox/radio matched by *selector*."""
        ...

    def click(self, selector: str) -> None:
        """Click the element matched by *selector*."""
        ...

    def set_input_files(self, selector: str, path: str) -> None:
        """Attach the file at *path* to the file input matched by *selector*."""
        ...

    def query(self, selector: str) -> bool:
        """Return ``True`` if an element matching *selector* exists."""
        ...

    def text(self, selector: str) -> str:
        """Return the visible text content of the element at *selector*."""
        ...


class FakePage:
    """Offline test double for :class:`Page`.

    Construct with the set of selectors that should be considered
    "present" on the page (``query()`` is otherwise deterministic and
    returns ``False``). Every driver call is appended, in order, to
    ``self.actions`` as a ``(method, selector, value)`` tuple so tests
    can assert on the exact action sequence.

    Acting on a selector that isn't in ``present_selectors`` raises
    :class:`AutofillError` — this is what lets tests exercise the
    "missing required field" / skip path without a real DOM.
    """

    def __init__(
        self,
        present_selectors: set[str] | None = None,
        *,
        texts: dict[str, str] | None = None,
    ) -> None:
        self.present_selectors: set[str] = set(present_selectors or set())
        self._texts: dict[str, str] = dict(texts or {})
        self.actions: list[tuple[str, str, str]] = []
        self.url: str = ""

    def _require(self, method: str, selector: str) -> None:
        if selector not in self.present_selectors:
            raise AutofillError(
                f"{method}({selector!r}) failed: no such element on the page"
            )

    def goto(self, url: str) -> None:
        self.url = url
        self.actions.append(("goto", url, ""))

    def fill(self, selector: str, value: str) -> None:
        self._require("fill", selector)
        self.actions.append(("fill", selector, value))

    def select_option(self, selector: str, value: str) -> None:
        self._require("select_option", selector)
        self.actions.append(("select_option", selector, value))

    def check(self, selector: str) -> None:
        self._require("check", selector)
        self.actions.append(("check", selector, ""))

    def click(self, selector: str) -> None:
        self._require("click", selector)
        self.actions.append(("click", selector, ""))

    def set_input_files(self, selector: str, path: str) -> None:
        self._require("set_input_files", selector)
        self.actions.append(("set_input_files", selector, path))

    def query(self, selector: str) -> bool:
        return selector in self.present_selectors

    def text(self, selector: str) -> str:
        self._require("text", selector)
        return self._texts.get(selector, "")


# ------------------------------------------------------------------- fields

@dataclass
class FormField:
    """A single concrete form field to be (or that was) filled."""

    selector: str
    kind: str  # "text" | "select" | "checkbox" | "file" | "click"
    value: str = ""
    label: str = ""
    required: bool = False
    filled: bool = False


@dataclass
class AutofillResult:
    """Outcome of attempting to autofill one application form."""

    ats: str
    url: str
    filled: list[FormField] = field(default_factory=list)
    skipped: list[FormField] = field(default_factory=list)
    requires_user: list[str] = field(default_factory=list)
    success: bool = False
    notes: str = ""


# --------------------------------------------------------------- autofiller

@runtime_checkable
class Autofiller(Protocol):
    """Strategy for filling out one ATS's application form."""

    name: str

    def fill(
        self,
        page: Page,
        profile: Any,
        answers: dict[str, str],
    ) -> AutofillResult:
        """Fill out the application form on *page* for *profile*.

        ``answers`` is a free-form dict of question-key -> answer the
        user pre-supplied (e.g. ``{"work_authorization": "yes"}``) used
        to fill anything the mapper can't resolve directly from the
        profile.
        """
        ...
