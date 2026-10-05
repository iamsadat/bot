"""Pure, browser-free logic for filling hosted application forms.

Everything here works on plain data, so it is unit-testable offline:

* :func:`split_name`: full name to (first, last).
* :func:`match_answer_key`: a question label to one of the ``answers`` keys
  (``work_authorization``, ``notice_period``, ``expected_ctc``, ...).
* :func:`choose_option`: pick the select/radio option that matches an answer,
  or a "Decline to self-identify" style option for EEO questions.
* :func:`classify_control`: what a scanned form control is (first name,
  résumé upload, a custom question, ...).
* :func:`plan_fill`: turn the scanned controls of a page plus an application
  ``plan`` into concrete :class:`Step` objects and a list of required
  questions a human must answer. Nothing is ever invented: when no answer is
  known for a required question, it lands in ``unfilled_required``.
* :func:`looks_submitted` / :func:`captcha_challenge`: classify page state.

The browser side (``jobhunt.autofill.apply``) scans the DOM into the control
dicts consumed here and executes the returned steps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Answer keys the server may send in ``plan["answers"]``.
ANSWER_KEYS = (
    "work_authorization", "requires_sponsorship", "years_experience", "notice_period",
    "current_ctc", "expected_ctc", "salary_expectation", "willing_to_relocate",
    "linkedin", "github", "website", "how_did_you_hear", "gender", "race",
    "veteran_status", "disability_status", "default_yes_no",
)

# Demographic / self-identification questions. With no explicit answer we pick
# a "decline" option when the form offers one; we never guess a demographic.
EEO_KEYS = frozenset({
    "gender", "race", "veteran_status", "disability_status", "sexual_orientation",
})

LINK_KEYS = ("linkedin", "github", "website", "portfolio")


# --------------------------------------------------------------- text helpers

def norm(text: Any) -> str:
    """Lowercase, drop required-markers/punctuation, collapse whitespace."""
    s = str(text or "").lower().replace("✱", " ").replace("*", " ")
    s = s.replace("’", "'").replace("‘", "'").replace("–", "-")
    s = re.sub(r"[_]+", " ", s)
    s = re.sub(r"[^a-z0-9+/&()'\- ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def split_name(full_name: str) -> tuple[str, str]:
    """Split a full name into ``(first, last)``.

    ``"Jane Marie Doe"`` -> ``("Jane", "Marie Doe")``; ``"Doe, Jane"`` ->
    ``("Jane", "Doe")``; a single word -> ``(word, "")``.
    """
    s = re.sub(r"\s+", " ", str(full_name or "")).strip()
    if not s:
        return "", ""
    if "," in s:
        last, _, first = s.partition(",")
        first, last = first.strip(), last.strip()
        if first and last:
            return first, last
        s = (first or last)
    parts = s.split(" ")
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def short_label(label: str, limit: int = 140) -> str:
    """Display form of a question label: long preambles collapse to the last
    sentence (where the actual question usually is), else get truncated."""
    s = re.sub(r"\s+", " ", str(label or "")).strip()
    if len(s) <= limit:
        return s
    sentences = [x.strip() for x in re.split(r"(?<=[.?!:])\s+", s) if x.strip()]
    if sentences and 10 <= len(sentences[-1]) <= limit:
        return sentences[-1]
    return s[: limit - 1].rstrip() + "…"


def as_answer_text(value: Any) -> str:
    """Render an answer value as form text (bools become Yes/No)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(as_answer_text(v) for v in value if v not in (None, ""))
    return str(value).strip()


# ------------------------------------------------------ label -> answer key

_CTC_WORDS = r"(\bctc\b|compensation|salary|\bpay\b|package|remuneration|\bfixed\b|\bcash\b)"

# Ordered: first match wins. More specific rules sit above broader ones
# (sponsorship before work authorization, CTC before generic salary, ...).
_KEY_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    # (key, any-of patterns, must-also-match patterns)
    ("disability_status", (r"disabilit", r"\bdisabled\b"), ()),
    ("veteran_status", (r"veteran", r"military (service|status)", r"armed forces"), ()),
    ("sexual_orientation", (r"sexual orientation", r"\blgbt", r"transgender"), ()),
    ("race", (r"\brace\b", r"ethnicit", r"hispanic", r"\blatin[oax]"), ()),
    ("gender", (r"\bgender\b", r"^sex\b", r"\bsex$"), ()),
    ("requires_sponsorship", (r"sponsor",), ()),
    ("requires_sponsorship", (r"\bvisa\b",), (r"requir|need",)),
    ("work_authorization", (
        r"authori[sz]ed to work", r"authori[sz]ation to work", r"work authori[sz]ation",
        r"eligib(le|ility) to work", r"right to work", r"permitted to work",
        r"legally (authori[sz]ed|eligible|able|permitted|allowed)", r"work permit",
    ), ()),
    ("willing_to_relocate", (r"relocat",), ()),
    ("notice_period", (
        r"notice period", r"\bnotice\b", r"how soon can you (join|start)",
        r"earliest (start|joining|date|you can)", r"joining (time|date|period)",
        r"when can you (join|start)",
    ), ()),
    ("current_ctc", (_CTC_WORDS,),
     (r"\bcurrent|\bpresent|\bexisting|last drawn|\bcurrently\b",)),
    ("expected_ctc", (r"\bctc\b",), (r"expect|desired|asking",)),
    ("salary_expectation", (_CTC_WORDS,), (r"expect|desired|asking|requirement|range",)),
    ("years_experience", (
        r"years? of (\w+ ){0,2}experience", r"years experience", r"how many years",
        r"total (years of )?experience", r"experience \(?in years", r"\byoe\b",
        r"work experience \(years",
    ), ()),
    ("how_did_you_hear", (
        r"how did you (hear|find|learn|come|get to know|discover)",
        r"where did you (hear|find|learn|see|discover)", r"how did you come across",
        r"referral source", r"how you heard", r"source of (this )?application",
    ), ()),
    ("linkedin", (r"linkedin",), ()),
    ("github", (r"github",), ()),
    ("portfolio", (r"portfolio",), ()),
    ("website", (r"website", r"personal (site|url|page)", r"\bblog\b"), ()),
    ("current_company", (
        r"current (company|employer|organi[sz]ation)",
        r"(current|present|most recent) or (previous|past|most recent) (employer|company)",
        r"(present|most recent|latest) (employer|company)",
    ), ()),
    ("current_title", (
        r"current (job )?(title|designation|position)",
        r"(current|present|most recent) or (previous|past) (job )?(title|designation)",
        r"(most recent|latest) (job )?(title|designation)",
    ), ()),
    ("country_of_residence", (r"country",), (r"resid|\blive\b|based|located|currently",)),
)

# A years-of-experience question about one specific skill ("...with React")
# is not answered with total experience - that would be an invented claim.
_SKILL_SPECIFIC = re.compile(r"\b(with|using)\b")


def _eeo_key(s: str) -> str | None:
    """EEO key for a (possibly long) label; the last-mentioned category wins.

    Long self-ID prompts often list every protected class in a preamble
    ("...without regard to gender, race, disability... select your gender:"),
    and the actual question comes last.
    """
    best, best_pos = None, -1
    for key, any_of, _ in _KEY_RULES:
        if key not in EEO_KEYS:
            continue
        for p in any_of:
            for m in re.finditer(p, s):
                if m.start() > best_pos:
                    best, best_pos = key, m.start()
    return best


def match_answer_key(label: str) -> str | None:
    """Map a question label to an answer key, or ``None`` if unknown."""
    s = norm(label)
    if not s:
        return None
    eeo = _eeo_key(s)
    if eeo:
        return eeo
    for key, any_of, all_of in _KEY_RULES:
        if any(re.search(p, s) for p in any_of) and all(re.search(p, s) for p in all_of):
            if key == "years_experience" and _SKILL_SPECIFIC.search(s):
                return None
            return key
    return None


# ------------------------------------------------------------------ options

_DECLINE_RE = re.compile(
    r"decline|prefer not|not to (say|disclose|answer|self|identify|specify|state)"
    r"|(don'?t|do not|choose not to|rather not|not) (wish|want|like) to"
    r"|choose not|rather not|no answer|not specified|undisclosed"
)
_YES = {"yes", "y", "true", "1", "yep", "yeah", "authorized", "authorised"}
_NO = {"no", "n", "false", "0", "nope"}


def is_decline_option(text: str) -> bool:
    return bool(_DECLINE_RE.search(norm(text)))


def _polarity(text: str) -> str | None:
    s = norm(text)
    if not s:
        return None
    if s in _YES:
        return "yes"
    if s in _NO:
        return "no"
    first = re.split(r"[\s,.\-/()]+", s)[0]
    if first in ("yes", "y"):
        return "yes"
    if first in ("no", "n", "not"):
        return "no"
    return None


def is_yes_no(options: list[str]) -> bool:
    """True when the options include both a yes-like and a no-like answer."""
    return {_polarity(o) for o in options} >= {"yes", "no"}


_UNIT_DAYS = (("year", 365), ("month", 30), ("week", 7), ("day", 1))


def _range_of(text: str) -> tuple[float, float] | None:
    """Numeric span an option/answer covers, e.g. '3-5 years' -> (3, 5)."""
    s = norm(text)
    if re.search(r"\bimmediate|\bimmediately|\bnone\b|\bfresher\b|serving", s):
        return (0.0, 0.0)
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", s)]
    if not nums:
        return None
    if re.search(r"less than|under|below|<|upto|up to|within|maximum|max", s):
        return (0.0, nums[0])
    if re.search(r"\+|more than|over|above|or more|at least|minimum|>", s):
        return (nums[0], float("inf"))
    if len(nums) >= 2 and re.search(r"\d\s*(-|to|–)\s*\d", s):
        return (nums[0], nums[1])
    return (nums[0], nums[0])


def _unit(text: str) -> int | None:
    s = norm(text)
    for word, days in _UNIT_DAYS:
        if word in s:
            return days
    return None


def _numeric_choice(options: list[str], answer: str) -> str | None:
    ans = _range_of(answer)
    if ans is None:
        return None
    a_unit = _unit(answer)
    value = ans[0]
    best = None
    for opt in options:
        rng = _range_of(opt)
        if rng is None:
            continue
        o_unit = _unit(opt)
        lo, hi = rng
        v = value
        if a_unit and o_unit and a_unit != o_unit:
            v = value * a_unit / o_unit
        if lo <= v <= hi:
            if best is None:
                best = opt
            elif lo == hi == v:  # an exact point beats a range
                best = opt
    return best


def choose_option(options: list[str], answer: Any, *, key: str | None = None) -> str | None:
    """Pick the option text that best matches *answer*.

    Falls back to a decline-style option for EEO keys when no answer was
    supplied (or none matched). Returns ``None`` when nothing fits — callers
    must then leave the question to the human.
    """
    opts = [o for o in (str(x).strip() for x in options) if o]
    if not opts:
        return None
    ans = as_answer_text(answer)
    if ans:
        n_ans = norm(ans)
        for o in opts:
            if norm(o) == n_ans:
                return o
        pol = _polarity(ans)
        if pol:
            same = [o for o in opts if _polarity(o) == pol]
            exact = [o for o in same if norm(o) == pol]
            if exact:
                return exact[0]
            if len(same) == 1:
                return same[0]
        num = _numeric_choice(opts, ans)
        if num:
            return num
        contains = [o for o in opts if n_ans and (n_ans in norm(o))]
        if len(contains) == 1:
            return contains[0]
        inside = [o for o in opts if len(norm(o)) >= 3 and norm(o) in n_ans]
        if len(inside) == 1:
            return inside[0]
        starts = [o for o in opts if norm(o).startswith(n_ans)]
        if len(starts) == 1:
            return starts[0]
    if key in EEO_KEYS:
        for o in opts:
            if is_decline_option(o):
                return o
    return None


def best_option_index(texts: list[str], target: str, *, role: str | None = None) -> int | None:
    """Index of the rendered dropdown option to click for *target*.

    Used for lazily-rendered menus (react-select, autocomplete lists) after
    typing *target*: exact match, then prefix ("India +91"), then substring.
    A location autocomplete takes the first suggestion as a last resort.
    """
    n_target = norm(target)
    normed = [norm(t) for t in texts]
    if not normed or not n_target:
        return None
    for test in (lambda o: o == n_target,
                 lambda o: o.startswith(n_target),
                 lambda o: n_target in o):
        hits = [i for i, o in enumerate(normed) if o and test(o)]
        if hits:
            return hits[0]
    if role == "location" and not re.match(r"no (options|results)|loading", normed[0]):
        return 0
    return None


# ---------------------------------------------------------- answer lookup

def _first_present(mapping: dict, *keys: str) -> str:
    for k in keys:
        v = as_answer_text(mapping.get(k))
        if v:
            return v
    return ""


def country_from_location(location: str) -> str:
    """'Bengaluru, Karnataka, India' -> 'India' (last comma part)."""
    parts = [p.strip() for p in str(location or "").split(",") if p.strip()]
    return parts[-1] if len(parts) >= 2 else ""


_PHONE_COUNTRY = (
    ("+971", "United Arab Emirates"), ("+91", "India"), ("+44", "United Kingdom"),
    ("+49", "Germany"), ("+33", "France"), ("+65", "Singapore"), ("+61", "Australia"),
    ("+353", "Ireland"), ("+31", "Netherlands"), ("+1", "United States"),
)


def phone_country(phone: str, location: str = "") -> str:
    """Best-effort country for a phone-country picker (from +prefix or location)."""
    p = re.sub(r"[^\d+]", "", str(phone or ""))
    if p.startswith("+"):
        for prefix, country in _PHONE_COUNTRY:
            if p.startswith(prefix):
                return country
    return country_from_location(location)


def lookup_answer(key: str | None, plan: dict) -> str:
    """Resolve the answer text for *key* from *plan* ('' when unknown)."""
    if not key:
        return ""
    answers = plan.get("answers") or {}
    links = plan.get("links") or {}
    applicant = plan.get("applicant") or {}
    if key in LINK_KEYS:
        return _first_present(links, key) or _first_present(answers, key)
    if key == "expected_ctc":
        return _first_present(answers, "expected_ctc", "salary_expectation")
    if key == "salary_expectation":
        return _first_present(answers, "salary_expectation", "expected_ctc")
    if key == "country_of_residence":
        return _first_present(answers, "country_of_residence", "country") or \
            country_from_location(applicant.get("location", ""))
    return _first_present(answers, key)


# ------------------------------------------------------------ classification

TEXTLIKE = frozenset({"text", "email", "tel", "url", "number", "search", "textarea", ""})


def _idn(ctrl: dict) -> str:
    return f"{ctrl.get('id', '')} {ctrl.get('name', '')}".lower()


def classify_control(ctrl: dict) -> str | None:
    """Return the semantic role of a scanned control.

    Identity roles: ``first_name``, ``last_name``, ``preferred_name``,
    ``full_name``, ``email``, ``phone``, ``phone_country``, ``location``,
    ``resume``, ``cover_letter_file``, ``cover_letter_text``,
    ``current_company``; otherwise an answer key from
    :func:`match_answer_key`, or ``None``.
    """
    ctype = ctrl.get("type", "")
    idn = _idn(ctrl)
    lab = norm(ctrl.get("label", ""))
    ac = (ctrl.get("autocomplete") or "").lower()

    if ctype == "file":
        if "cover" in idn or "cover" in lab:
            return "cover_letter_file"
        if re.search(r"resume|\bcv\b", idn) or re.search(r"resume|\bcv\b|curriculum", lab):
            return "resume"
        return None

    if ctype == "combobox" and (ctrl.get("group") == "phone" or ctrl.get("id") == "country"):
        if lab in ("country", "country code", "phone country") or ctrl.get("id") == "country":
            return "phone_country"

    if ctype in TEXTLIKE or ctype == "combobox":
        if "preferred" in lab and "name" in lab:
            return "preferred_name"
        if re.search(r"(^|[\s\[_])first_?name\b", idn) or ac == "given-name" or \
                re.match(r"^(legal )?(first|given) name", lab):
            return "first_name"
        if re.search(r"(^|[\s\[_])last_?name\b", idn) or ac == "family-name" or \
                re.match(r"^(legal )?(last|family) name|^surname", lab):
            return "last_name"
        if ctrl.get("name") == "name" or lab in ("full name", "name", "legal name", "your name",
                                                 "full legal name"):
            return "full_name"
        if ctype == "email" or re.search(r"(^|[\s\[_])email\b", idn) or \
                re.match(r"^(e-?mail|email address)\b", lab):
            return "email"
        if ctype == "tel" or re.search(r"(^|[\s\[_])phone\b", idn) or \
                re.match(r"^(phone|mobile|contact number|phone number|cell)\b", lab):
            return "phone"
        if ctrl.get("name") in ("location", "job_application[location]") or \
                ctrl.get("id") in ("location", "candidate-location", "job_application_location") or \
                lab in ("location", "current location", "location (city)", "city",
                        "current city", "location city"):
            return "location"
        if ctrl.get("name") == "org":
            return "current_company"
        if ctype == "textarea" and ("cover_letter" in idn or "cover letter" in lab or
                                    ctrl.get("name") == "comments"):
            return "cover_letter_text"
    return match_answer_key(ctrl.get("label", ""))


# ------------------------------------------------------------------ planning

@dataclass
class Step:
    """One concrete action the browser executor should take."""

    action: str  # "fill" | "select" | "combobox" | "check" | "upload" | "cover_text"
    handle: str  # data-jh handle of the control (or of the chosen option)
    label: str
    value: str = ""
    role: str | None = None
    required: bool = False
    ctrl: dict = field(default_factory=dict)
    # Combobox whose options only exist once its menu is open: the executor
    # opens it and picks with choose_option(rendered texts, value, key=role).
    choose_in_ui: bool = False
    # ``value`` is the owner's default_yes_no: only apply it if the rendered
    # options turn out to be a plain yes/no pair.
    yes_no_default: bool = False


_CONSENT_RE = re.compile(
    r"\b(consent|agree|acknowledge|accept|certify|attest|privacy|permission)\b"
    r"|\bauthori[sz]e (us|the company)\b")


def _identity_value(role: str, plan: dict) -> str:
    applicant = plan.get("applicant") or {}
    answers = plan.get("answers") or {}
    first, last = split_name(applicant.get("name", ""))
    if role == "preferred_name":
        return _first_present(answers, "preferred_name") or first
    if role == "first_name":
        return first
    if role == "last_name":
        return last
    if role == "full_name":
        return as_answer_text(applicant.get("name"))
    if role in ("email", "phone", "location"):
        return as_answer_text(applicant.get(role))
    if role == "phone_country":
        return phone_country(applicant.get("phone", ""), applicant.get("location", ""))
    if role == "cover_letter_text":
        return as_answer_text(plan.get("cover_letter_text"))
    return ""


def plan_fill(controls: list[dict], plan: dict) -> tuple[list[Step], list[str]]:
    """Decide what to do with every scanned control.

    *controls* are dicts produced by the page scan: ``handle``, ``type``
    (``text``/``email``/``tel``/``textarea``/``select``/``combobox``/
    ``radio``/``checkbox``/``file``), ``id``, ``name``, ``label``,
    ``required``, ``value`` (current), ``options`` (list of
    ``{"handle", "label"}`` for select/radio/checkbox/combobox) and
    ``group`` (container hint, e.g. ``"phone"``).

    Returns ``(steps, unfilled_required)``. File uploads are ordered first
    (Lever re-parses a résumé into the contact fields on upload).
    """
    steps: list[Step] = []
    unfilled: list[str] = []
    answers = plan.get("answers") or {}
    default_yes_no = as_answer_text(answers.get("default_yes_no"))
    has_resume = bool(plan.get("resume_pdf"))
    cover = as_answer_text(plan.get("cover_letter_text"))
    has_cover_textarea = any(classify_control(c) == "cover_letter_text" for c in controls)

    for ctrl in controls:
        label = short_label(ctrl.get("label") or ctrl.get("name") or ctrl.get("id")
                            or "(unlabelled field)")
        required = bool(ctrl.get("required"))
        ctype = ctrl.get("type", "")
        role = classify_control(ctrl)
        options = ctrl.get("options") or []
        opt_labels = [o.get("label", "") for o in options]

        def need_human(_label: str = label, _required: bool = required) -> None:
            if _required and _label not in unfilled:
                unfilled.append(_label)

        if ctype == "file":
            if role == "resume" and has_resume:
                steps.append(Step("upload", ctrl["handle"], label, "resume", role, required, ctrl))
            elif role == "cover_letter_file" and cover and not has_cover_textarea:
                steps.append(Step("upload", ctrl["handle"], label, "cover_letter", role,
                                  required, ctrl))
            elif not ctrl.get("value"):
                need_human()
            continue

        if role in ("first_name", "last_name", "preferred_name", "full_name", "email",
                    "phone", "location", "phone_country", "cover_letter_text"):
            value = _identity_value(role, plan)
            if not value:
                if not ctrl.get("value"):
                    need_human()
                continue
            if ctype == "combobox":
                steps.append(Step("combobox", ctrl["handle"], label, value, role, required, ctrl))
            elif ctype == "select":
                pick = choose_option(opt_labels, value, key=role)
                if pick:
                    steps.append(Step("select", ctrl["handle"], label, pick, role, required, ctrl))
                else:
                    need_human()
            elif role == "cover_letter_text":
                steps.append(Step("cover_text", ctrl["handle"], label, value, role, required,
                                  ctrl))
            else:
                steps.append(Step("fill", ctrl["handle"], label, value, role, required, ctrl))
            continue

        answer = lookup_answer(role, plan)
        # The owner's default_yes_no only answers required, non-EEO yes/no
        # questions, and never gives consent on the candidate's behalf.
        use_default = bool(default_yes_no and required and role not in EEO_KEYS
                           and not _CONSENT_RE.search(norm(ctrl.get("label", ""))))
        if ctype == "combobox" and not opt_labels:
            # Options render lazily: choose among them in the browser.
            if answer or role in EEO_KEYS:
                steps.append(Step("combobox", ctrl["handle"], label, answer, role, required,
                                  ctrl, choose_in_ui=True))
            elif use_default and not ctrl.get("value"):
                steps.append(Step("combobox", ctrl["handle"], label, default_yes_no, role,
                                  required, ctrl, choose_in_ui=True, yes_no_default=True))
            elif not ctrl.get("value"):
                need_human()
            continue
        if ctype in ("select", "combobox", "radio") or (ctype == "checkbox" and len(options) > 1):
            pick = choose_option(opt_labels, answer, key=role) if opt_labels else None
            if pick is None and not answer and use_default and is_yes_no(opt_labels):
                pick = choose_option(opt_labels, default_yes_no)
            if pick is None:
                if not ctrl.get("value"):
                    need_human()
                continue
            if ctype == "combobox":
                steps.append(Step("combobox", ctrl["handle"], label, pick, role, required, ctrl))
            elif ctype == "select":
                steps.append(Step("select", ctrl["handle"], label, pick, role, required, ctrl))
            else:
                opt = next(o for o in options if o.get("label", "") == pick)
                steps.append(Step("check", opt["handle"], label, pick, role, required, ctrl))
            continue

        if ctype == "checkbox":
            # A lone checkbox is almost always a consent/attestation: never tick
            # it on the candidate's behalf.
            if required or _CONSENT_RE.search(norm(label)):
                need_human()
            continue

        # Free-text question.
        if answer:
            steps.append(Step("fill", ctrl["handle"], label, answer, role, required, ctrl))
        elif not ctrl.get("value"):
            need_human()

    steps.sort(key=lambda s: 0 if s.action == "upload" else 1)
    return steps, unfilled


# --------------------------------------------------------- page-state checks

_CONFIRM_TEXT = re.compile(
    r"thank(s| you) for (applying|your application|submitting)"
    r"|application (has been |was )?(successfully )?(submitted|received)"
    r"|we('ve| have) received your application"
    r"|successfully (submitted|applied)|your application is (in|complete)",
    re.IGNORECASE,
)


def looks_submitted(url: str, body_text: str = "", *, form_present: bool = True) -> bool:
    """Heuristic: does the page show a post-submit confirmation?

    Greenhouse navigates to ``.../confirmation``; Lever to ``.../thanks``.
    Confirmation copy only counts once the application form is gone, so a
    job description that happens to say "thank you for applying" doesn't.
    """
    path = re.sub(r"[?#].*$", "", str(url or "")).lower()
    if re.search(r"/confirmation/?$|/job_app/confirmation|/thanks/?$|/thank-you/?$", path):
        return True
    if not form_present and _CONFIRM_TEXT.search(body_text or ""):
        return True
    return False


def captcha_challenge(frames: list[dict]) -> str | None:
    """Return a description of a *visible* CAPTCHA widget/challenge, else None.

    *frames* are ``{"src", "width", "height", "visible"}`` dicts for the page's
    iframes. Invisible score-based badges (reCAPTCHA v3/Enterprise, Lever's
    invisible hCaptcha) only count once they pop an actual challenge.
    """
    for f in frames:
        src = str(f.get("src") or "").lower()
        if not f.get("visible") or float(f.get("width") or 0) < 60 or \
                float(f.get("height") or 0) < 60:
            continue
        if "recaptcha" in src and ("bframe" in src or
                                   ("anchor" in src and "size=invisible" not in src)):
            return "reCAPTCHA"
        if "hcaptcha" in src and ("frame=challenge" in src or
                                  ("frame=checkbox" in src and "invisible" not in src)):
            return "hCaptcha"
        if "challenges.cloudflare.com" in src or "turnstile" in src:
            return "Cloudflare Turnstile"
    return None
