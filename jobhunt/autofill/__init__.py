"""jobhunt.autofill — browser autofill for application forms.

* :func:`browser_apply` / :func:`supports_browser_apply` fill a company's
  hosted Greenhouse or Lever application form in a real browser: co-pilot
  (the human submits), auto (clicks submit) or dry run (network-guarded).
  This is the only way a candidate can apply on those ATSes; their apply
  APIs need the employer's key.
* The older spec-driven layer (Workday, iCIMS, generic) drives forms through
  a small ``Page`` Protocol and stays unit-testable offline via ``FakePage``.
"""

from jobhunt.autofill.apply import (
    browser_apply,
    resolve_application_url,
    supports_browser_apply,
)
from jobhunt.autofill.base import (
    Autofiller,
    AutofillError,
    AutofillResult,
    FakePage,
    FormField,
    Page,
)
from jobhunt.autofill.driver import PlaywrightPage, autofill_application
from jobhunt.autofill.generic import GenericAutofiller
from jobhunt.autofill.icims import IcimsAutofiller
from jobhunt.autofill.mapper import execute_fields, map_profile_to_fields
from jobhunt.autofill.registry import AutofillRegistry
from jobhunt.autofill.workday import WorkdayAutofiller

__all__ = [
    "Autofiller",
    "AutofillError",
    "AutofillRegistry",
    "AutofillResult",
    "FakePage",
    "FormField",
    "GenericAutofiller",
    "IcimsAutofiller",
    "Page",
    "PlaywrightPage",
    "WorkdayAutofiller",
    "autofill_application",
    "browser_apply",
    "execute_fields",
    "map_profile_to_fields",
    "resolve_application_url",
    "supports_browser_apply",
]
