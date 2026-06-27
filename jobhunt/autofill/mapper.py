"""Resolve concrete :class:`~jobhunt.autofill.base.FormField` values.

``map_profile_to_fields`` takes a ``UserProfile``, a free-form
``answers`` dict the user pre-supplied (work authorization, years of
experience, LinkedIn URL, ...), and a list of ``field_specs`` describing
a form's known fields (one dict per field — selector/label/kind/required
and optional matching hints). It returns concrete ``FormField`` objects
with ``value`` resolved wherever possible.

Resolution order per field:
  1. Heuristic match against well-known profile attributes (name parts,
     email, phone, location, LinkedIn, resume upload) by inspecting the
     field's ``label`` (case-insensitive) and/or an explicit ``key`` hint.
  2. Lookup in ``answers`` by ``key`` (or a normalized version of the
     label) when the profile has no opinion.
  3. Anything left unresolved — including anything that looks like an
     EEO / veteran / disability / demographic / open-ended essay
     question — is **never guessed**. Its label is appended to
     ``requires_user`` so a human reviews it before submission.
"""

from __future__ import annotations

import re
from typing import Any

from jobhunt.autofill.base import FormField

# Labels/keys that must always be deferred to a human, no matter what is
# in `answers` — these are sensitive, ATS-specific, or too open-ended to
# answer automatically (self-ID forms, EEO surveys, essay questions...).
_SENSITIVE_PATTERNS = (
    "eeo",
    "equal employment",
    "veteran",
    "disability",
    "race",
    "ethnicity",
    "gender identity",
    "sexual orientation",
    "demographic",
    "self-identif",
    "self identif",
)


def _normalize(label: str) -> str:
    """Lowercase, strip, collapse whitespace/punctuation to single spaces."""
    s = label.strip().lower()
    s = re.sub(r"[_\-]+", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_sensitive(norm_label: str) -> bool:
    return any(pat in norm_label for pat in _SENSITIVE_PATTERNS)


def _split_name(full_name: str) -> tuple[str, str]:
    """Split a full name into (first, last). Best-effort, stdlib-only."""
    parts = full_name.strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _resolve_from_profile(norm_label: str, key: str, profile: Any) -> str | None:
    """Return a resolved value from *profile* for a well-known field, or
    ``None`` if this field isn't one we can confidently resolve from the
    profile (i.e. the caller should fall back to ``answers``)."""

    first, last = _split_name(getattr(profile, "name", "") or "")

    candidates = {
        ("first_name",): first,
        ("last_name",): last,
        ("full_name", "name"): getattr(profile, "name", "") or "",
        ("email",): getattr(profile, "email", "") or "",
        ("location", "city", "address"): (
            getattr(profile, "locations", None) or [""]
        )[0],
    }

    for keys, value in candidates.items():
        if key in keys:
            return value

    # Label-based heuristics (used when no explicit `key` hint matched).
    if any(tok in norm_label for tok in ("first name", "given name")):
        return first
    if any(tok in norm_label for tok in ("last name", "surname", "family name")):
        return last
    if norm_label in ("full name", "name", "legal name") or "full name" in norm_label:
        return getattr(profile, "name", "") or ""
    if "email" in norm_label:
        return getattr(profile, "email", "") or ""
    if any(tok in norm_label for tok in ("location", "city", "address")):
        locs = getattr(profile, "locations", None) or []
        return locs[0] if locs else ""

    return None


def map_profile_to_fields(
    profile: Any,
    answers: dict[str, str],
    field_specs: list[dict[str, Any]],
) -> tuple[list[FormField], list[str]]:
    """Resolve *field_specs* into concrete :class:`FormField` objects.

    Each item in ``field_specs`` is a dict with keys:
      - ``selector`` (str, required)
      - ``kind`` (str, required) — "text" | "select" | "checkbox" | "file" | "click"
      - ``label`` (str, required) — human label, used for matching
      - ``required`` (bool, optional, default False)
      - ``key`` (str, optional) — explicit semantic hint, e.g.
        "first_name", "email", "resume", "linkedin", "work_authorization".
        When omitted, the label alone drives matching.

    Returns ``(fields, requires_user)`` where ``fields`` is the list of
    ``FormField`` (value populated when resolvable, ``value=""`` and
    ``filled=False`` otherwise) and ``requires_user`` lists the labels of
    fields that were deliberately left for a human (sensitive/unknown
    questions) — these are intentionally *not* included with a guessed
    value in ``fields``.
    """
    fields: list[FormField] = []
    requires_user: list[str] = []

    # Normalize answers keys once so lookups are insensitive to
    # "work_authorization" vs "work authorization" vs "Work Authorization".
    normalized_answers = {_normalize(k): v for k, v in answers.items()}

    for spec in field_specs:
        selector = spec["selector"]
        kind = spec["kind"]
        label = spec.get("label", "")
        required = bool(spec.get("required", False))
        key = _normalize(spec.get("key", "") or "")
        norm_label = _normalize(label)

        form_field = FormField(
            selector=selector,
            kind=kind,
            label=label,
            required=required,
        )

        # Action buttons (e.g. "Save and Continue") aren't data fields —
        # there's nothing to resolve, just an action to take later.
        if kind == "click":
            form_field.value = key or norm_label
            fields.append(form_field)
            continue

        # Sensitive/demographic questions are always deferred — even if
        # an answer happens to exist for them, we don't auto-fill EEO
        # style data.
        if _is_sensitive(norm_label) or _is_sensitive(key):
            requires_user.append(label or selector)
            fields.append(form_field)
            continue

        # File uploads (resume/cover letter) — resolved via a dedicated
        # `key` hint since profile/answers carry a path, not free text.
        if kind == "file":
            value = normalized_answers.get(key) or normalized_answers.get("resume") or ""
            if value:
                form_field.value = value
            else:
                requires_user.append(label or selector)
            fields.append(form_field)
            continue

        # 1) Try the profile heuristics for well-known identity fields.
        resolved = _resolve_from_profile(norm_label, key, profile)

        # 2) LinkedIn / phone / free-form questions come from `answers`.
        if resolved is None or resolved == "":
            answer_value = None
            if key and key in normalized_answers:
                answer_value = normalized_answers[key]
            elif norm_label in normalized_answers:
                answer_value = normalized_answers[norm_label]
            elif "linkedin" in norm_label or key == "linkedin":
                answer_value = normalized_answers.get("linkedin")
            elif "phone" in norm_label or key == "phone":
                answer_value = normalized_answers.get("phone") or getattr(profile, "phone", None)

            resolved = answer_value

        if resolved:
            form_field.value = str(resolved)
        else:
            # Could not confidently resolve from profile or answers.
            # Don't guess — defer to the human.
            requires_user.append(label or selector)

        fields.append(form_field)

    return fields, requires_user
