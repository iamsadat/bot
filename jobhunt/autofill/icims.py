"""iCIMS ATS autofiller.

iCIMS-hosted career portals (``*.icims.com``) typically render plain
``name``/``id`` attributes rather than data-automation hooks. The
selectors below follow iCIMS's common ``iCIMS_...`` naming convention
closely enough to demonstrate correct structure against a fake page.
"""

from __future__ import annotations

import logging
from typing import Any

from jobhunt.autofill.base import AutofillResult, FormField, Page
from jobhunt.autofill.mapper import map_profile_to_fields

logger = logging.getLogger(__name__)

ICIMS_FIELD_SPECS: list[dict[str, Any]] = [
    {
        "selector": "input[name='iCIMS_FirstName']",
        "kind": "text",
        "label": "First Name",
        "key": "first_name",
        "required": True,
    },
    {
        "selector": "input[name='iCIMS_LastName']",
        "kind": "text",
        "label": "Last Name",
        "key": "last_name",
        "required": True,
    },
    {
        "selector": "input[name='iCIMS_Email']",
        "kind": "text",
        "label": "Email",
        "key": "email",
        "required": True,
    },
    {
        "selector": "input[name='iCIMS_Phone']",
        "kind": "text",
        "label": "Phone",
        "key": "phone",
        "required": False,
    },
    {
        "selector": "input[name='iCIMS_City']",
        "kind": "text",
        "label": "City",
        "key": "location",
        "required": False,
    },
    {
        "selector": "input[name='iCIMS_LinkedIn']",
        "kind": "text",
        "label": "LinkedIn URL",
        "key": "linkedin",
        "required": False,
    },
    {
        "selector": "input[name='iCIMS_Resume']",
        "kind": "file",
        "label": "Upload Resume",
        "key": "resume",
        "required": True,
    },
    {
        "selector": "select[name='iCIMS_WorkAuthorization']",
        "kind": "select",
        "label": "Work Authorization",
        "key": "work_authorization",
        "required": True,
    },
    {
        "selector": "input[name='iCIMS_EEORace']",
        "kind": "select",
        "label": "Race/Ethnicity (EEO)",
        "key": "race_ethnicity",
        "required": False,
    },
    {
        "selector": "input[name='iCIMS_SubmitApplication']",
        "kind": "click",
        "label": "Submit Application",
        "key": "submit",
        "required": True,
    },
]


class IcimsAutofiller:
    """Fills out an iCIMS-hosted application form."""

    name = "icims"

    def fill(self, page: Page, profile: Any, answers: dict[str, str]) -> AutofillResult:
        url = getattr(page, "url", "") or ""
        page.goto(url)

        fields, requires_user = map_profile_to_fields(profile, answers, ICIMS_FIELD_SPECS)

        filled: list[FormField] = []
        skipped: list[FormField] = []

        for f in fields:
            if f.label in requires_user:
                skipped.append(f)
                continue
            if not page.query(f.selector):
                skipped.append(f)
                continue

            try:
                if f.kind == "text":
                    page.fill(f.selector, f.value)
                elif f.kind == "select":
                    page.select_option(f.selector, f.value)
                elif f.kind == "checkbox":
                    page.check(f.selector)
                elif f.kind == "file":
                    page.set_input_files(f.selector, f.value)
                elif f.kind == "click":
                    page.click(f.selector)
                else:
                    skipped.append(f)
                    continue
            except Exception:
                logger.debug("autofill field %r (%s) failed", f.selector, f.kind, exc_info=True)
                skipped.append(f)
                continue

            f.filled = True
            filled.append(f)

        required_ok = all(f.filled for f in filled + skipped if f.required)
        success = required_ok and not any(f.required for f in skipped)

        notes = f"icims: filled={len(filled)} skipped={len(skipped)} requires_user={len(requires_user)}"

        return AutofillResult(
            ats=self.name,
            url=url,
            filled=filled,
            skipped=skipped,
            requires_user=requires_user,
            success=success,
            notes=notes,
        )
