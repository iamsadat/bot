"""Fill a company's hosted Greenhouse / Lever application form in a browser.

Why a browser: the Greenhouse Job Board apply endpoint needs HTTP Basic Auth
with the *employer's* API key, and Lever's POST apply needs the employer's
``?key=``. A candidate can only apply through the hosted form, so we drive it.

Modes (see :func:`browser_apply`):

* **co-pilot** (``submit=False, keep_open=True``): a visible browser opens
  with the form filled; the human reviews and clicks Submit. A daemon thread
  keeps the browser alive and reports back through ``on_finish`` when the
  window closes (``submitted`` is detected from the confirmation page).
* **auto** (``submit=True``): same filler, then clicks the real submit button
  and waits for the confirmation. Never solves CAPTCHAs.
* **dry run** (``dry_run=True``): fills only, and a network guard aborts
  every request that is not GET/HEAD/OPTIONS, so nothing can be submitted.

Safety: nothing in the fill path clicks a submit-type control (every click
goes through :meth:`_Filler._safe_click`, which refuses submit buttons); the
single submit click lives in :meth:`_Filler.submit`, reached only when
``submit=True`` and not a dry run.

Playwright's sync objects must stay on the thread that created them, so the
whole browser session (launch, fill, keep-open watch, close) runs inside one
worker thread; the immediate result is handed back through a queue.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from jobhunt.autofill.questions import (
    Step,
    best_option_index,
    captcha_challenge,
    choose_option,
    is_yes_no,
    looks_submitted,
    plan_fill,
    short_label,
    split_name,
)

log = logging.getLogger(__name__)

INSTALL_HINT = "pip install playwright && playwright install chromium"

_GH_HOST = re.compile(r"^(job-)?boards(\.eu)?\.greenhouse\.io$")
_GH_API_HOST = re.compile(r"^boards-api(\.eu)?\.greenhouse\.io$")
_LEVER_HOST = re.compile(r"^jobs(\.eu)?\.lever\.co$")
_DIGITS = re.compile(r"^\d{4,}$")

FORM_SELECTOR = (
    "#application-form, #application_form, form[action*='/applications'], "
    "form[data-qa='application-form']"
)
_SUBMIT_SELECTORS = (
    "#application-form button[type=submit]",   # Greenhouse (job-boards, React)
    "#submit_app",                             # Greenhouse classic
    "#application_form [type=submit]",
    "#btn-submit",                             # Lever
    "button[data-qa='btn-submit']",
)


# ------------------------------------------------------------------ routing

def _query(url: str) -> dict[str, str]:
    try:
        return {k: v[0] for k, v in parse_qs(urlparse(url).query).items() if v}
    except ValueError:
        return {}


def resolve_application_url(url: str) -> str | None:
    """Return the hosted application-form URL for a posting, or ``None``.

    * Lever ``jobs.lever.co/<site>/<id>`` -> ``.../<id>/apply``.
    * Greenhouse ``(job-)boards(.eu).greenhouse.io/<board>/jobs/<id>`` and the
      Board API URL -> ``job-boards(.eu).greenhouse.io/embed/job_app?for=<board>
      &token=<id>``. The embed form is used because boards with a custom
      careers page redirect ``/<board>/jobs/<id>`` back to the company site.
    * Any company page carrying ``gh_jid=<id>`` -> ``boards.greenhouse.io/embed/
      job_app?token=<id>``, which Greenhouse redirects to the board's form.
    """
    url = str(url or "").strip()
    if not url:
        return None
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    parts = [p for p in parsed.path.split("/") if p]
    query = _query(parsed.geturl())
    eu = ".eu." in f".{host}"

    if _LEVER_HOST.match(host):
        if len(parts) >= 2 and re.match(r"^[0-9a-f-]{20,}$", parts[1], re.IGNORECASE):
            base = f"https://{host}/{parts[0]}/{parts[1]}/apply"
            return f"{base}?{parsed.query}" if parsed.query else base
        return None

    gh_embed = f"https://job-boards{'.eu' if eu else ''}.greenhouse.io/embed/job_app"
    if _GH_HOST.match(host):
        if parts[:2] == ["embed", "job_app"] and _DIGITS.match(query.get("token", "")):
            if query.get("for"):
                return f"{gh_embed}?{urlencode({'for': query['for'], 'token': query['token']})}"
            return f"https://{host}/embed/job_app?{urlencode({'token': query['token']})}"
        if len(parts) >= 3 and parts[1] == "jobs" and _DIGITS.match(parts[2]):
            return f"{gh_embed}?{urlencode({'for': parts[0], 'token': parts[2]})}"
        if _DIGITS.match(query.get("gh_jid", "")):
            board = parts[0] if parts and parts[0] != "embed" else ""
            if board:
                return f"{gh_embed}?{urlencode({'for': board, 'token': query['gh_jid']})}"
            return f"https://{host}/embed/job_app?{urlencode({'token': query['gh_jid']})}"
        return None
    if _GH_API_HOST.match(host):
        # /v1/boards/<board>/jobs/<id>
        if len(parts) >= 5 and parts[1] == "boards" and parts[3] == "jobs" \
                and _DIGITS.match(parts[4]):
            return f"{gh_embed}?{urlencode({'for': parts[2], 'token': parts[4]})}"
        return None

    gh_jid = query.get("gh_jid", "")
    if _DIGITS.match(gh_jid):
        return f"https://boards.greenhouse.io/embed/job_app?{urlencode({'token': gh_jid})}"
    return None


def supports_browser_apply(url: str) -> bool:
    """True for Greenhouse/Lever postings whose hosted form we can fill.

    Pure (no network): Greenhouse hosted boards, company pages carrying
    ``gh_jid=``, and Lever ``jobs.lever.co`` postings.
    """
    return resolve_application_url(url) is not None


def _form_url_candidates(hosted: str) -> list[str]:
    """The hosted URL plus, for a bare gh_jid, the EU-region fallback."""
    out = [hosted]
    if hosted.startswith("https://boards.greenhouse.io/embed/job_app?token="):
        out.append(hosted.replace("https://boards.", "https://boards.eu.", 1))
    return out


# ---------------------------------------------------------------- DOM scan

_SCAN_JS = r"""
(formSel) => {
  const root = document.querySelector(formSel) || document.body;
  const clean = s => (s || '').replace(/[✱*]/g, ' ').replace(/\s+/g, ' ').trim();
  const STRIP = 'input,select,textarea,option,button,svg,script,style,ul,.visually-hidden,' +
    '[aria-hidden="true"],.required,.asterisk,.dropdown-container,.resume-upload-working,' +
    '.resume-upload-success,.resume-upload-failure,.application-answer-alternative';
  const textOf = el => {
    if (!el) return '';
    const c = el.cloneNode(true);
    c.querySelectorAll(STRIP).forEach(n => n.remove());
    return clean(c.textContent);
  };
  const starIn = el => !!el && (/[✱*]/.test(el.textContent || '') ||
                                 !!el.querySelector('.required, .asterisk'));
  const byIds = ids => (ids || '').split(/\s+/).filter(Boolean)
    .map(i => document.getElementById(i)).filter(Boolean);
  const shown = el => {
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 || r.height > 0)) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  const kindOf = el => {
    if (el.tagName === 'SELECT') return 'select';
    if (el.tagName === 'TEXTAREA') return 'textarea';
    if ((el.getAttribute('role') || '') === 'combobox') return 'combobox';
    return (el.getAttribute('type') || 'text').toLowerCase();
  };
  const labelFor = (el, kind) => {
    const lever = el.closest('li.application-question, .application-question');
    if (lever) {
      const l = lever.querySelector('.application-label');
      if (l) { const t = textOf(l.querySelector('.text') || l); if (t) return [t, starIn(l)]; }
    }
    if (kind === 'radio' || kind === 'checkbox' || kind === 'file') {
      const g = el.closest('fieldset, [role="group"], [role="radiogroup"]');
      if (g && !g.classList.contains('phone-input')) {
        const lg = g.querySelector(':scope > legend') ||
                   byIds(g.getAttribute('aria-labelledby'))[0];
        const t = textOf(lg);
        if (t) return [t, starIn(lg) || g.getAttribute('aria-required') === 'true'];
      }
    }
    const lb = byIds(el.getAttribute('aria-labelledby'));
    if (lb.length) {
      const t = lb.map(textOf).join(' ').trim();
      if (t) return [t, lb.some(starIn)];
    }
    if (el.id && kind !== 'file' && kind !== 'radio' && kind !== 'checkbox') {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      const t = textOf(l);
      if (t) return [t, starIn(l)];
    }
    const wrap = el.closest('label');
    if (wrap && kind !== 'radio' && kind !== 'checkbox') {
      const t = textOf(wrap); if (t) return [t, starIn(wrap)];
    }
    const field = el.closest('.field, .form-field, .application-field');
    if (field) {
      const l = field.querySelector('label, legend, .label');
      const t = textOf(l); if (t) return [t, starIn(l)];
    }
    return [clean(el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
                  el.name || el.id || ''), false];
  };
  const optionLabel = el => {
    let l = el.id ? document.querySelector('label[for="' + CSS.escape(el.id) + '"]') : null;
    l = l || el.closest('label');
    return textOf(l) || clean(el.value);
  };
  // Static option lists for Greenhouse's React (react-select) questions.
  const remixOpts = {};
  try {
    const ld = (window.__remixContext && window.__remixContext.state &&
                window.__remixContext.state.loaderData) || {};
    for (const v of Object.values(ld)) {
      const jp = v && v.jobPost;
      if (!jp) continue;
      const qs = [].concat(jp.questions || [],
        ...((jp.eeoc_sections || []).map(s => s.questions || [])));
      for (const q of qs) for (const f of (q.fields || [])) {
        if (f && f.name && Array.isArray(f.values) && f.values.length)
          remixOpts[f.name] = f.values.map(x => String(x.label));
      }
    }
  } catch (e) {}

  let next = Number(document.documentElement.getAttribute('data-jh-next') || '0');
  const handleOf = el => {
    let h = el.getAttribute('data-jh'), fresh = false;
    if (!h) { h = String(next++); el.setAttribute('data-jh', h); fresh = true; }
    return [h, fresh];
  };
  const out = [];
  const groups = {};
  for (const el of root.querySelectorAll('input, select, textarea')) {
    const kind = kindOf(el);
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(kind)) continue;
    if (!el.id && !el.name) continue;
    if (el.getAttribute('aria-hidden') === 'true' && el.tabIndex < 0) continue;
    if (/captcha/i.test((el.name || '') + ' ' + (el.id || ''))) continue;
    if (el.disabled) continue;
    const isChoice = kind === 'radio' || kind === 'checkbox';
    const vis = kind === 'file' ? true :
      (shown(el) || (isChoice && el.closest('label') && shown(el.closest('label'))) ||
       (isChoice && el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]') &&
        shown(document.querySelector('label[for="' + CSS.escape(el.id) + '"]'))));
    if (!vis) continue;
    const [label, star] = labelFor(el, kind);
    const required = !!(el.required || el.getAttribute('aria-required') === 'true' || star ||
      el.closest('[aria-required="true"]') || el.closest('.required-field'));
    const [handle, fresh] = handleOf(el);
    const group = el.closest('fieldset.phone-input, .phone-input') ? 'phone' : '';
    if (isChoice) {
      const key = el.name || el.id;
      let g = groups[key];
      if (!g) {
        g = {handle, type: kind, id: el.id || '', name: el.name || '', autocomplete: '',
             label, required, value: '', options: [], group, fresh: false};
        groups[key] = g; out.push(g);
      }
      g.required = g.required || required;
      g.fresh = g.fresh || fresh;
      const ol = optionLabel(el);
      g.options.push({handle, label: ol, value: el.value, checked: el.checked});
      if (el.checked) g.value = g.value ? g.value + ', ' + ol : ol;
      continue;
    }
    let value = '', options = [];
    if (kind === 'select') {
      options = [...el.options].filter(o => o.value !== '' && !o.disabled)
        .map(o => ({handle: '', label: clean(o.textContent), value: o.value}));
      value = el.value ? clean((el.selectedOptions[0] || {}).textContent || el.value) : '';
    } else if (kind === 'combobox') {
      const box = el.closest('.select__control') || el.closest('.select-shell') ||
                  el.closest('[class*="-control"]');
      const sv = box && box.querySelector('.select__single-value, .select__multi-value__label');
      value = sv ? clean(sv.textContent) : '';
      options = (remixOpts[el.id] || []).map(l => ({handle: '', label: l, value: l}));
    } else if (kind === 'file') {
      value = el.files && el.files.length ? el.files[0].name : '';
    } else {
      value = el.value || '';
    }
    out.push({handle, type: kind, id: el.id || '', name: el.name || '',
              autocomplete: el.getAttribute('autocomplete') || '', label, required, value,
              options, group, fresh});
  }
  document.documentElement.setAttribute('data-jh-next', String(next));
  return out;
}
"""

_FRAMES_JS = r"""
() => [...document.querySelectorAll('iframe')].map(f => {
  const r = f.getBoundingClientRect();
  let vis = r.width > 0 && r.height > 0 && r.bottom > 0 && r.top > -1000;
  for (let p = f; vis && p; p = p.parentElement) {
    const s = getComputedStyle(p);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') vis = false;
  }
  return {src: f.src || '', width: r.width, height: r.height, visible: vis};
})
"""

_CAPTCHA_PRESENT_JS = r"""
() => !!document.querySelector('script[src*="recaptcha"], script[src*="hcaptcha"], ' +
  'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], script[src*="turnstile"]')
"""

# True when clicking *el* could submit the form. Every fill-time click is
# checked against this; only Filler.submit() may click a submit control.
_SUBMITTISH_JS = r"""
el => {
  const b = el.closest('button, input[type=submit], input[type=image], [role=button]');
  if (!b) return false;
  let t = (b.getAttribute('type') || '').toLowerCase();
  if (!t && b.tagName === 'BUTTON') t = 'submit';
  if (t === 'submit' || t === 'image') return true;
  return /\b(submit|send application|apply now)\b/i.test(b.innerText || b.value || '');
}
"""

_ERRORS_JS = r"""
() => [...document.querySelectorAll('.helper-text--error, [id$="-error"], .error-message, ' +
  '.application-error, .field-error, .error')]
  .filter(e => e.offsetParent !== null && (e.innerText || '').trim())
  .map(e => e.innerText.trim()).slice(0, 5)
"""


def _sel(handle: str) -> str:
    return f'[data-jh="{handle}"]'


def _safe_filename(name: str, suffix: str) -> str:
    first, last = split_name(name)
    stem = "_".join(p for p in (first, last.replace(" ", "_")) if p)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "", stem)[:60]
    return f"{stem}_{suffix}" if stem else suffix


class _Filler:
    """Drives one Playwright page: load, scan, fill, (optionally) submit."""

    def __init__(self, page: Any, plan: dict, tmpdir: str) -> None:
        self.page = page
        self.plan = plan
        self.tmpdir = tmpdir
        self.filled: list[str] = []
        self.done_roles: set[str] = set()
        self._fill_checks: list[Step] = []

    # ---------------------------------------------------------- loading
    def load(self, hosted: str) -> str | None:
        """Navigate to the form; return an error string, or None on success."""
        last = ""
        for url in _form_url_candidates(hosted):
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                self.page.wait_for_selector(FORM_SELECTOR, state="attached", timeout=20_000)
            except Exception as exc:  # noqa: BLE001 - try the next candidate
                last = str(exc).splitlines()[0][:200]
                continue
            try:
                self.page.wait_for_load_state("load", timeout=15_000)
            except Exception:  # noqa: BLE001 - slow third-party assets
                pass
            self.page.wait_for_timeout(800)  # let React hydrate the form
            return None
        where = self.page.url or hosted
        if "error=true" in where or "job_board" in where:
            return f"posting is closed or not found ({where})"
        return f"no application form found at {where} ({last})"

    # ------------------------------------------------------------- scan
    def scan(self) -> list[dict]:
        return self.page.evaluate(_SCAN_JS, FORM_SELECTOR)

    # ----------------------------------------------------------- clicks
    def _safe_click(self, locator: Any) -> None:
        if locator.evaluate(_SUBMITTISH_JS):
            raise RuntimeError("refusing to click a submit control while filling")
        locator.click()

    # ------------------------------------------------------------ files
    def _file_path(self, which: str) -> str:
        applicant = self.plan.get("applicant") or {}
        if which == "resume":
            path = Path(self.tmpdir) / _safe_filename(applicant.get("name", ""), "Resume.pdf")
            if not path.exists():
                data = self.plan.get("resume_pdf") or b""
                path.write_bytes(data if isinstance(data, bytes) else bytes(data))
            return str(path)
        path = Path(self.tmpdir) / _safe_filename(applicant.get("name", ""), "Cover_Letter.txt")
        if not path.exists():
            path.write_text(str(self.plan.get("cover_letter_text") or ""), encoding="utf-8")
        return str(path)

    def _lever_wait_resume_parse(self) -> None:
        """Lever re-reads an uploaded résumé into the contact fields; wait it out."""
        if "lever.co" not in (self.page.url or ""):
            return
        try:
            self.page.wait_for_selector(
                ".resume-upload-success, .resume-upload-failure",
                state="visible", timeout=15_000)
        except Exception:  # noqa: BLE001
            pass

    def _cover_letter_textarea(self) -> bool:
        """Greenhouse hides the cover-letter textarea behind 'Enter manually'."""
        page = self.page
        toggle = page.locator("[data-testid='cover_letter-text'], "
                              "#cover_letter_fieldset a[data-source='paste']")
        if toggle.count() == 0:
            return False
        self._safe_click(toggle.first)
        area = page.locator("textarea#cover_letter_text, textarea[name='cover_letter_text']")
        area.first.wait_for(state="visible", timeout=5_000)
        area.first.fill(str(self.plan.get("cover_letter_text") or ""))
        return True

    # -------------------------------------------------------- comboboxes
    def _menu_options(self, cid: str, timeout_s: float) -> Any:
        """Locator for the open react-select/listbox menu's options, or None."""
        page = self.page
        scoped = page.locator(f'[id^="react-select-{cid}-option"]') if cid else None
        generic = page.locator(".select__menu [role='option'], [role='listbox'] [role='option']")
        deadline = time.monotonic() + timeout_s
        while True:
            for loc in (scoped, generic):
                if loc is not None and loc.count() > 0:
                    return loc
            if time.monotonic() >= deadline:
                return None
            page.wait_for_timeout(250)

    def _combobox(self, step: Step) -> bool:
        page = self.page
        inp = page.locator(_sel(step.handle))
        cid = step.ctrl.get("id", "")
        location = step.role == "location"
        query = step.value.split(",")[0].strip() if location else step.value
        inp.scroll_into_view_if_needed()
        self._safe_click(inp)
        idx = None
        options = None
        if not step.choose_in_ui:
            inp.fill("")
            inp.press_sequentially(query[:60], delay=20)
            options = self._menu_options(cid, 8 if location else 4)
            if options is not None:
                if location:
                    page.wait_for_timeout(600)  # async suggestions settle
                idx = best_option_index(options.all_inner_texts(), query, role=step.role)
        if idx is None and not location:
            # Open the unfiltered menu and choose among everything it renders.
            inp.fill("")
            options = self._menu_options(cid, 1.5)
            if options is None:
                inp.press("ArrowDown")  # opens a react-select menu; never submits
                options = self._menu_options(cid, 3)
            if options is not None:
                texts = [t.strip() for t in options.all_inner_texts()]
                if step.yes_no_default and not is_yes_no(texts):
                    idx = None
                elif step.choose_in_ui:
                    pick = choose_option(texts, step.value, key=step.role)
                    idx = texts.index(pick) if pick is not None else None
                else:
                    idx = best_option_index(texts, step.value, role=step.role)
        if options is None or idx is None:
            self._abandon_combobox(inp)
            return False
        self._safe_click(options.nth(idx))
        page.wait_for_timeout(150)
        chosen = inp.evaluate(
            "el => { const b = el.closest('.select__control, .select-shell') || "
            "el.parentElement.parentElement; const v = b && b.querySelector("
            "'.select__single-value, .select__multi-value__label'); "
            "return v ? v.textContent : ''; }")
        return bool((chosen or "").strip())

    @staticmethod
    def _abandon_combobox(inp: Any) -> None:
        """Leave an unmatched combobox empty rather than holding stray typed text."""
        try:
            inp.fill("")
            inp.press("Escape")
        except Exception:  # noqa: BLE001
            pass

    def _lever_location(self, step: Step) -> None:
        """Pick the first Lever location suggestion (best effort)."""
        page = self.page
        results = page.locator(".dropdown-results .dropdown-location")
        try:
            results.first.wait_for(state="visible", timeout=5_000)
            self._safe_click(results.first)
        except Exception:  # noqa: BLE001 - typed text stays as the answer
            pass

    # ------------------------------------------------------------- steps
    def execute(self, steps: list[Step]) -> None:
        for step in steps:
            try:
                ok = self._execute_one(step)
            except Exception as exc:  # noqa: BLE001 - one field never sinks the form
                log.info("autofill: %s failed: %s", step.label, str(exc).splitlines()[0])
                ok = False
            if ok:
                if step.role:
                    self.done_roles.add(step.role)
                if step.label not in self.filled:
                    self.filled.append(step.label)

    def _execute_one(self, step: Step) -> bool:
        page = self.page
        loc = page.locator(_sel(step.handle))
        if step.action == "upload":
            if step.value == "cover_letter":
                try:
                    if self._cover_letter_textarea():
                        return True
                except Exception as exc:  # noqa: BLE001 - fall back to a file
                    log.info("autofill: cover-letter textarea unavailable: %s",
                             str(exc).splitlines()[0])
                loc.set_input_files(self._file_path("cover_letter"))
                return True
            loc.set_input_files(self._file_path("resume"))
            self._lever_wait_resume_parse()
            return True
        if step.action in ("fill", "cover_text"):
            loc.scroll_into_view_if_needed()
            loc.fill(step.value)
            if step.role == "location" and step.ctrl.get("name") == "location":
                self._lever_location(step)
            self._fill_checks.append(step)
            return True
        if step.action == "select":
            loc.select_option(label=step.value)
            return True
        if step.action == "check":
            try:
                loc.check(timeout=3_000)
            except Exception:  # noqa: BLE001 - custom-styled inputs: click the label
                label = page.locator(f'label:has({_sel(step.handle)})')
                if label.count() == 0:
                    raise
                self._safe_click(label.first)
            return loc.is_checked()
        if step.action == "combobox":
            return self._combobox(step)
        return False

    def recheck_text(self) -> None:
        """Re-fill text inputs a page script overwrote (e.g. Lever résumé parse)."""
        for step in self._fill_checks:
            try:
                loc = self.page.locator(_sel(step.handle))
                if loc.count() and loc.input_value() != step.value and step.role != "location":
                    loc.fill(step.value)
            except Exception:  # noqa: BLE001
                continue

    # ------------------------------------------------------------ captcha
    def visible_captcha(self) -> str | None:
        try:
            return captcha_challenge(self.page.evaluate(_FRAMES_JS))
        except Exception:  # noqa: BLE001
            return None

    def captcha_present(self) -> bool:
        try:
            return bool(self.page.evaluate(_CAPTCHA_PRESENT_JS))
        except Exception:  # noqa: BLE001
            return False

    def is_submitted(self) -> bool:
        try:
            url = self.page.url
            form_present = self.page.locator(FORM_SELECTOR).count() > 0
            text = "" if form_present else self.page.inner_text("body", timeout=3_000)[:20_000]
            return looks_submitted(url, text, form_present=form_present)
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------- submit
    def submit(self, *, wait_s: float = 45.0) -> tuple[bool, str]:
        """Click the real submit button (auto mode only) and await confirmation."""
        page = self.page
        challenge = self.visible_captcha()
        if challenge:
            return False, f"{challenge} challenge is showing; not attempting to solve it"
        button = None
        for sel in _SUBMIT_SELECTORS:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible():
                button = loc.first
                break
        if button is None:
            return False, "could not find the form's submit button"
        button.scroll_into_view_if_needed()
        button.click()
        deadline = time.monotonic() + wait_s
        errors_seen_at = None
        while time.monotonic() < deadline:
            page.wait_for_timeout(1_000)
            if self.is_submitted():
                return True, "application submitted (confirmation page detected)"
            challenge = self.visible_captcha()
            if challenge:
                return False, (f"{challenge} challenge appeared after clicking submit; "
                               "not solved automatically, a human must finish this one")
            errors = page.evaluate(_ERRORS_JS)
            if errors:
                errors_seen_at = errors_seen_at or time.monotonic()
                if time.monotonic() - errors_seen_at > 4:
                    return False, "form rejected the submission: " + "; ".join(errors)[:300]
        return False, f"clicked submit but no confirmation appeared within {int(wait_s)}s"


# --------------------------------------------------------- browser session

def _launch_browser(pw: Any, *, headless: bool) -> Any:
    kwargs: dict[str, Any] = {"headless": headless}
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if exe:
        kwargs["executable_path"] = exe
    return pw.chromium.launch(**kwargs)


def _new_context(browser: Any, *, headless: bool, dry_run: bool) -> Any:
    kwargs: dict[str, Any] = {
        "service_workers": "block" if dry_run else "allow",
        "accept_downloads": False,
    }
    if headless:
        kwargs["viewport"] = {"width": 1280, "height": 1000}
    else:
        kwargs["no_viewport"] = True
    return browser.new_context(**kwargs)


def _merge_unique(*lists: list[str]) -> list[str]:
    out: list[str] = []
    for lst in lists:
        for item in lst:
            if item and item not in out:
                out.append(item)
    return out


class _Session:
    """One browser session, run entirely on its own worker thread."""

    def __init__(self, plan: dict, hosted: str, *, mode: str, submit: bool, headless: bool,
                 keep_open: bool, dry_run: bool, on_finish: Callable[[dict], None] | None,
                 screenshot_path: str | None) -> None:
        self.plan = plan
        self.hosted = hosted
        self.mode = mode
        self.submit = submit
        self.headless = headless
        self.keep_open = keep_open
        self.dry_run = dry_run
        self.on_finish = on_finish
        self.screenshot_path = screenshot_path
        self.results: queue.Queue[dict] = queue.Queue(maxsize=1)
        self.cancel = threading.Event()
        self.blocked: list[str] = []

    def _base(self) -> dict:
        return {"ok": False, "mode": self.mode, "filled": [], "unfilled_required": [],
                "submitted": False, "detail": ""}

    def _guard(self, route: Any) -> None:
        """Dry-run network guard: only GET/HEAD/OPTIONS may leave the browser."""
        try:
            request = route.request
            method = request.method.upper()
            if method in ("GET", "HEAD", "OPTIONS"):
                route.fallback()
                return
            self.blocked.append(f"{method} {request.url}")
            log.warning("dry-run guard blocked %s %s", method, request.url)
            route.abort("blockedbyclient")
        except Exception:  # noqa: BLE001 - page closing mid-request
            pass

    def run(self) -> None:
        result = self._base()
        pw = browser = page = None
        tmpdir = tempfile.mkdtemp(prefix="jobhunt-apply-")
        filler: _Filler | None = None
        try:
            from playwright.sync_api import sync_playwright

            pw = sync_playwright().start()
            browser = _launch_browser(pw, headless=self.headless)
            context = _new_context(browser, headless=self.headless, dry_run=self.dry_run)
            if self.dry_run:
                context.route("**/*", self._guard)
            page = context.new_page()
            page.set_default_timeout(8_000)
            filler = _Filler(page, self.plan, tmpdir)
            result = self._fill_and_maybe_submit(filler)
        except Exception as exc:  # noqa: BLE001 - never raise to the caller
            result["detail"] = f"browser apply failed: {str(exc).splitlines()[0][:300]}"
            if filler is not None:
                result["filled"] = list(filler.filled)
        if self.dry_run:
            result["blocked_requests"] = list(self.blocked)
        if self.screenshot_path and page is not None:
            try:
                page.screenshot(path=self.screenshot_path, full_page=True)
            except Exception:  # noqa: BLE001
                pass
        self.results.put(result)

        try:
            if self.keep_open and page is not None and not self.cancel.is_set():
                self._watch(page, browser, filler, already=bool(result.get("submitted")))
        finally:
            for closer in (getattr(browser, "close", None), getattr(pw, "stop", None)):
                try:
                    if closer is not None:
                        closer()
                except Exception:  # noqa: BLE001
                    pass
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _fill_and_maybe_submit(self, filler: _Filler) -> dict:
        result = self._base()
        error = filler.load(self.hosted)
        if error:
            result["detail"] = error
            return result
        controls = filler.scan()
        steps, prelim = plan_fill(controls, self.plan)
        filler.execute(steps)
        # Second pass: questions revealed by earlier answers (e.g. EEO follow-ups).
        fresh = [c for c in filler.scan() if c.get("fresh")]
        if fresh:
            steps2, prelim2 = plan_fill(fresh, self.plan)
            # The cover letter / résumé were already handled (e.g. the textarea
            # that 'Enter manually' revealed); don't redo them under a new label.
            cover = {"cover_letter_file", "cover_letter_text"}
            done = filler.done_roles

            def _repeat(step: Step) -> bool:
                if step.role == "resume":
                    return "resume" in done
                return step.role in cover and bool(done & cover)

            filler.execute([s for s in steps2 if not _repeat(s)])
            prelim = _merge_unique(prelim, prelim2)
        filler.recheck_text()
        try:
            final = filler.scan()
            unfilled = _merge_unique([
                short_label(c.get("label") or c.get("name") or c.get("id") or "")
                for c in final if c.get("required") and not str(c.get("value") or "")])
        except Exception:  # noqa: BLE001
            unfilled = prelim
        result["filled"] = list(filler.filled)
        result["unfilled_required"] = unfilled
        captcha_note = " The page uses a CAPTCHA." if filler.captcha_present() else ""
        need = (f" {len(unfilled)} required question(s) need you: {', '.join(unfilled)}."
                if unfilled else "")

        if self.mode == "dry_run":
            result["ok"] = True
            result["detail"] = (f"Dry run: filled {len(filler.filled)} field(s).{need}"
                                f" Blocked {len(self.blocked)} non-GET request(s); "
                                f"nothing was submitted.{captcha_note}")
            return result
        if not self.submit:
            result["ok"] = True
            result["detail"] = (f"Filled {len(filler.filled)} field(s).{need} Review the form "
                                f"and click Submit yourself.{captcha_note}")
            return result
        if unfilled:
            result["detail"] = f"Not submitted:{need}"
            return result
        submitted, detail = filler.submit()
        result["ok"] = submitted
        result["submitted"] = submitted
        result["detail"] = detail
        return result

    def _watch(self, page: Any, browser: Any, filler: _Filler | None, *, already: bool) -> None:
        """Keep the co-pilot window alive until the human closes it."""
        submitted = already
        max_s = float(os.environ.get("JOBHUNT_COPILOT_MAX_MINUTES", "120")) * 60
        # Once the confirmation shows, keep the window a little longer for the
        # human, then close and report so the outcome is recorded promptly.
        grace_s = float(os.environ.get("JOBHUNT_COPILOT_AFTER_SUBMIT_SECONDS", "120"))
        deadline = time.monotonic() + max_s
        detail = "browser closed"
        while not self.cancel.is_set():
            try:
                if page.is_closed() or not browser.is_connected():
                    break
                if time.monotonic() > deadline:
                    detail = ("window closed after the confirmation page" if submitted
                              else "co-pilot window timed out and was closed")
                    break
                page.wait_for_timeout(1_000)
                if not submitted and filler is not None and filler.is_submitted():
                    submitted = True
                    deadline = min(deadline, time.monotonic() + grace_s)
            except Exception:  # noqa: BLE001 - closed while waiting
                break
        if submitted:
            detail = f"application submitted; {detail}"
        else:
            detail = f"not submitted; {detail}"
        if self.on_finish is not None:
            try:
                self.on_finish({"submitted": submitted, "detail": detail})
            except Exception:  # noqa: BLE001
                log.exception("browser_apply on_finish callback failed")


# ------------------------------------------------------------------ public

def browser_apply(
    plan: dict,
    *,
    submit: bool,
    headless: bool,
    keep_open: bool,
    on_finish: Callable[[dict], None] | None = None,
    dry_run: bool = False,
    screenshot_path: str | None = None,
    timeout_s: float = 240.0,
) -> dict:
    """Fill (and optionally submit) the hosted application form for ``plan["url"]``.

    ``plan``: ``url``, ``applicant`` {name, email, phone, location},
    ``resume_pdf`` (bytes, optional), ``cover_letter_text``, ``answers``
    (see :data:`jobhunt.autofill.questions.ANSWER_KEYS`), ``links``
    {linkedin, github, website, portfolio}.

    * ``submit=False`` - co-pilot: fill only, the human submits.
    * ``submit=True`` - auto: click the real submit button and wait for the
      confirmation. Refuses when required questions are unanswered or a
      CAPTCHA challenge is showing.
    * ``keep_open=True`` - return right after filling while a daemon thread
      keeps the browser open; when the human closes it, ``on_finish`` gets
      ``{"submitted": bool, "detail": str}``. (``on_finish`` is only used in
      this mode.)
    * ``dry_run=True`` - abort every non-GET/HEAD/OPTIONS request and never
      click submit (``mode="dry_run"``; the result also lists
      ``blocked_requests``).

    Returns ``{"ok", "mode", "filled", "unfilled_required", "submitted",
    "detail"}``. Never raises for page/network errors.
    """
    mode = "dry_run" if dry_run else ("auto" if submit else "copilot")
    base = {"ok": False, "mode": mode, "filled": [], "unfilled_required": [],
            "submitted": False, "detail": ""}
    try:
        url = str((plan or {}).get("url") or "").strip()
        hosted = resolve_application_url(url)
        if not hosted:
            return {**base, "detail": ("unsupported URL for browser apply (Greenhouse or "
                                       f"Lever hosted postings only): {url!r}")}
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return {**base, "detail": f"Playwright is not installed: {INSTALL_HINT}"}

        session = _Session(plan, hosted, mode=mode, submit=bool(submit) and not dry_run,
                           headless=headless, keep_open=keep_open, dry_run=dry_run,
                           on_finish=on_finish, screenshot_path=screenshot_path)
        worker = threading.Thread(target=session.run, name="jobhunt-browser-apply",
                                  daemon=True)
        worker.start()
        try:
            result = session.results.get(timeout=timeout_s)
        except queue.Empty:
            session.cancel.set()
            return {**base, "detail": f"timed out after {int(timeout_s)}s filling the form"}
        if not keep_open:
            worker.join(timeout=30)
        return result
    except Exception as exc:  # noqa: BLE001 - contract: never raise
        return {**base, "detail": f"browser apply failed: {exc}"}
