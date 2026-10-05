"""Generic fallback autofiller.

Used for any application URL that isn't recognized as Workday or iCIMS.
Targets a minimal, vanilla-HTML-ish field set (plain ``#id`` selectors)
representative of a bespoke "Apply" form — enough to demonstrate the
same fill/skip/requires_user behavior without assuming any particular
ATS vendor's DOM conventions.
"""

from __future__ import annotations

from typing import Any

from jobhunt.autofill.base import AutofillResult, Page
from jobhunt.autofill.mapper import execute_fields, map_profile_to_fields

GENERIC_FIELD_SPECS: list[dict[str, Any]] = [
    {
        "selector": "#first_name",
        "kind": "text",
        "label": "First Name",
        "key": "first_name",
        "required": True,
    },
    {
        "selector": "#last_name",
        "kind": "text",
        "label": "Last Name",
        "key": "last_name",
        "required": True,
    },
    {
        "selector": "#email",
        "kind": "text",
        "label": "Email",
        "key": "email",
        "required": True,
    },
    {
        "selector": "#phone",
        "kind": "text",
        "label": "Phone",
        "key": "phone",
        "required": False,
    },
    {
        "selector": "#location",
        "kind": "text",
        "label": "Location",
        "key": "location",
        "required": False,
    },
    {
        "selector": "#linkedin_url",
        "kind": "text",
        "label": "LinkedIn",
        "key": "linkedin",
        "required": False,
    },
    {
        "selector": "#resume",
        "kind": "file",
        "label": "Resume",
        "key": "resume",
        "required": True,
    },
    {
        "selector": "#how_did_you_hear",
        "kind": "text",
        "label": "How did you hear about us?",
        "key": "how_did_you_hear",
        "required": False,
    },
    {
        "selector": "#gender_identity",
        "kind": "select",
        "label": "Gender Identity (Demographic)",
        "key": "gender_identity",
        "required": False,
    },
    {
        "selector": "#submit_application",
        "kind": "click",
        "label": "Submit Application",
        "key": "submit",
        "required": True,
    },
]


class GenericAutofiller:
    """Fallback autofiller for application forms from unrecognized ATSes."""

    name = "generic"

    def fill(
        self, page: Page, profile: Any, answers: dict[str, str], *, submit: bool = False
    ) -> AutofillResult:
        """Fill the form; action buttons are clicked only when ``submit=True``."""
        url = getattr(page, "url", "") or ""
        page.goto(url)

        fields, requires_user = map_profile_to_fields(profile, answers, GENERIC_FIELD_SPECS)

        filled, skipped, success = execute_fields(page, fields, requires_user, submit=submit)

        notes = f"generic: filled={len(filled)} skipped={len(skipped)} requires_user={len(requires_user)}"

        return AutofillResult(
            ats=self.name,
            url=url,
            filled=filled,
            skipped=skipped,
            requires_user=requires_user,
            success=success,
            notes=notes,
        )
