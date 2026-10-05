"""Real-browser autofill driver (Playwright) behind the Page protocol.

``PlaywrightPage`` adapts a Playwright sync page to the autofill ``Page``
protocol, so the existing Workday/iCIMS/generic autofillers drive a real
browser. ``autofill_application`` launches the browser (optional ``playwright``
dependency), navigates, runs the registry, and — only when ``submit=True`` —
clicks Next/Submit-type controls. Default is **co-pilot**: fill the form and
leave the final submit to the user (``keep_open=True`` keeps the filled
browser open for them).

The driver is offline-testable by injecting a ``FakePage``; the real browser
path is exercised by a Playwright-guarded test. For Greenhouse / Lever
postings use :func:`jobhunt.autofill.browser_apply` instead.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from jobhunt.autofill.base import AutofillResult, Page
from jobhunt.autofill.questions import looks_submitted
from jobhunt.autofill.registry import AutofillRegistry

log = logging.getLogger(__name__)

_SUBMIT_SELECTORS = (
    "button[type=submit]", "input[type=submit]",
    "[data-automation-id=bottom-navigation-next-button]", "#submit-app",
    "button:has-text('Submit')",
)


class PlaywrightPage:
    """Adapt a ``playwright.sync_api.Page`` to the autofill ``Page`` protocol."""

    def __init__(self, page: Any, *, action_timeout_ms: int = 4000) -> None:
        self._p = page
        try:
            page.set_default_timeout(action_timeout_ms)
        except Exception:
            pass

    @property
    def url(self) -> str:
        return getattr(self._p, "url", "") or ""

    def goto(self, url: str) -> None:
        self._p.goto(url)

    def fill(self, selector: str, value: str) -> None:
        self._p.fill(selector, value)

    def select_option(self, selector: str, value: str) -> None:
        self._p.select_option(selector, value)

    def check(self, selector: str) -> None:
        self._p.check(selector)

    def click(self, selector: str) -> None:
        self._p.click(selector)

    def set_input_files(self, selector: str, path: str) -> None:
        self._p.set_input_files(selector, path)

    def query(self, selector: str) -> bool:
        try:
            return self._p.query_selector(selector) is not None
        except Exception:
            return False

    def text(self, selector: str) -> str:
        el = self._p.query_selector(selector)
        return el.inner_text() if el is not None else ""


def _launch_real_page(headless: bool):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - optional dep
        raise RuntimeError(
            "real-browser autofill needs Playwright — pip install playwright "
            "&& playwright install chromium"
        ) from exc
    pw = sync_playwright().start()
    # Honor an explicit chromium binary (e.g. a pre-provisioned browser) so we
    # don't depend on `playwright install` in constrained environments.
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    launch_kwargs: dict = {"headless": headless}
    if exe:
        launch_kwargs["executable_path"] = exe
    browser = pw.chromium.launch(**launch_kwargs)
    page = browser.new_page()

    def close() -> None:
        try:
            browser.close()
        finally:
            pw.stop()

    return PlaywrightPage(page), close


def _attempt_submit(page: Page) -> bool:
    for sel in _SUBMIT_SELECTORS:
        if page.query(sel):
            try:
                page.click(sel)
                return True
            except Exception:
                continue
    return False


def _run(page: Page, url: str, profile: Any, answers: dict[str, str],
         registry: AutofillRegistry, submit: bool) -> AutofillResult:
    page.goto(url)
    result = registry.fill(page, url, profile, answers, submit=submit)
    if submit and not result.requires_user:
        if _attempt_submit(page):
            result.notes += " (submitted)"
        else:
            result.notes += " (no submit button found)"
    return result


def _run_kept_open(url: str, profile: Any, answers: dict[str, str], *,
                   registry: AutofillRegistry, submit: bool, headless: bool,
                   on_finish: Callable[[dict], None] | None,
                   timeout_s: float = 240.0) -> AutofillResult:
    """Fill in a worker thread and keep that browser open until the user closes it.

    Playwright's sync objects are bound to the thread that created them, so
    launch, fill, the keep-alive loop and close all happen on the worker; the
    fill result comes back through a queue.
    """
    results: queue.Queue = queue.Queue(maxsize=1)

    def worker() -> None:
        closer = None
        try:
            page, closer = _launch_real_page(headless)
            results.put(_run(page, url, profile, answers, registry, submit))
        except Exception as exc:  # noqa: BLE001 - re-raised on the caller's thread
            results.put(exc)
            if closer is not None:
                closer()
            return
        submitted = False
        raw = page._p
        deadline = time.monotonic() + float(
            os.environ.get("JOBHUNT_COPILOT_MAX_MINUTES", "120")) * 60
        try:
            while not raw.is_closed() and time.monotonic() < deadline:
                raw.wait_for_timeout(1_000)
                submitted = submitted or looks_submitted(raw.url)
        except Exception:  # noqa: BLE001 - closed while waiting
            pass
        finally:
            closer()
        if on_finish is not None:
            try:
                on_finish({"submitted": submitted,
                           "detail": "application submitted" if submitted else "browser closed"})
            except Exception:  # noqa: BLE001
                log.exception("autofill on_finish callback failed")

    threading.Thread(target=worker, name="jobhunt-autofill", daemon=True).start()
    item = results.get(timeout=timeout_s)
    if isinstance(item, Exception):
        raise item
    return item


def autofill_application(
    url: str,
    profile: Any,
    answers: dict[str, str],
    *,
    page: Page | None = None,
    submit: bool = False,
    headless: bool = True,
    registry: AutofillRegistry | None = None,
    limiter: Any = None,
    keep_open: bool = False,
    on_finish: Callable[[dict], None] | None = None,
) -> AutofillResult:
    """Autofill the application form at *url* (co-pilot by default).

    Pass ``page`` (e.g. a FakePage) to drive without a real browser. With
    ``submit=False`` (default) the form is filled but nothing that could
    submit (Next / Save and Continue / Submit) is ever clicked — the user
    confirms. ``submit=True`` clicks only when no field still
    ``requires_user``.

    ``keep_open=True`` (real browser only) leaves the filled browser open for
    the user after this returns; ``on_finish({"submitted", "detail"})`` runs
    when they close it. Without it the browser is closed on return.
    """
    if limiter is not None:
        limiter.acquire()
    registry = registry or AutofillRegistry()
    if page is not None:
        return _run(page, url, profile, answers, registry, submit)
    if keep_open:
        return _run_kept_open(url, profile, answers, registry=registry, submit=submit,
                              headless=headless, on_finish=on_finish)
    real_page, closer = _launch_real_page(headless)
    try:
        return _run(real_page, url, profile, answers, registry, submit)
    finally:
        closer()
