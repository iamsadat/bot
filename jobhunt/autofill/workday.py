"""Workday ATS autofiller.

Workday-hosted career sites (``*.myworkdayjobs.com``) render forms with
stable ``data-automation-id`` attributes, which is what real selectors
target in production. The selectors below are illustrative but follow
Workday's actual naming convention closely enough to demonstrate correct
structure.
"""

from __future__ import annotations

from typing import Any

from jobhunt.autofill.base import AutofillResult, Page
from jobhunt.autofill.mapper import execute_fields, map_profile_to_fields

# Field specs for Workday's "My Information" application step.
WORKDAY_FIELD_SPECS: list[dict[str, Any]] = [
    {
        "selector": "input[data-automation-id='legalNameSection_firstName']",
        "kind": "text",
        "label": "First Name",
        "key": "first_name",
        "required": True,
    },
    {
        "selector": "input[data-automation-id='legalNameSection_lastName']",
        "kind": "text",
        "label": "Last Name",
        "key": "last_name",
        "required": True,
    },
    {
        "selector": "input[data-automation-id='email']",
        "kind": "text",
        "label": "Email Address",
        "key": "email",
        "required": True,
    },
    {
        "selector": "input[data-automation-id='phone-number']",
        "kind": "text",
        "label": "Phone Number",
        "key": "phone",
        "required": False,
    },
    {
        "selector": "input[data-automation-id='addressSection_city']",
        "kind": "text",
        "label": "City",
        "key": "location",
        "required": False,
    },
    {
        "selector": "input[data-automation-id='linkedinQuestion']",
        "kind": "text",
        "label": "LinkedIn Profile",
        "key": "linkedin",
        "required": False,
    },
    {
        "selector": "input[data-automation-id='resumeUpload']",
        "kind": "file",
        "label": "Resume/CV",
        "key": "resume",
        "required": True,
    },
    {
        "selector": "select[data-automation-id='workAuthorization']",
        "kind": "select",
        "label": "Are you legally authorized to work in this country?",
        "key": "work_authorization",
        "required": True,
    },
    {
        "selector": "input[data-automation-id='eeoVeteranStatus']",
        "kind": "select",
        "label": "Veteran Status (EEO)",
        "key": "veteran_status",
        "required": False,
    },
    {
        "selector": "input[data-automation-id='eeoDisabilityStatus']",
        "kind": "select",
        "label": "Disability Status (EEO)",
        "key": "disability_status",
        "required": False,
    },
    {
        "selector": "button[data-automation-id='bottom-navigation-next-button']",
        "kind": "click",
        "label": "Save and Continue",
        "key": "submit",
        "required": True,
    },
]


class WorkdayAutofiller:
    """Fills out a Workday-hosted application form."""

    name = "workday"

    def fill(
        self, page: Page, profile: Any, answers: dict[str, str], *, submit: bool = False
    ) -> AutofillResult:
        """Fill the form; action buttons are clicked only when ``submit=True``."""
        url = getattr(page, "url", "") or ""
        page.goto(url)

        fields, requires_user = map_profile_to_fields(profile, answers, WORKDAY_FIELD_SPECS)

        filled, skipped, success = execute_fields(page, fields, requires_user, submit=submit)

        notes = f"workday: filled={len(filled)} skipped={len(skipped)} requires_user={len(requires_user)}"

        return AutofillResult(
            ats=self.name,
            url=url,
            filled=filled,
            skipped=skipped,
            requires_user=requires_user,
            success=success,
            notes=notes,
        )
