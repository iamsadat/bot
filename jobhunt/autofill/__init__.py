"""jobhunt.autofill — driver-agnostic ATS autofill layer.

Fills out application forms on ATS systems with no public API (Workday,
iCIMS, and a generic fallback) via a small ``Page`` Protocol that a real
Playwright driver can implement. Fully unit-testable offline through
``FakePage``.
"""

from jobhunt.autofill.base import (
    Autofiller,
    AutofillError,
    AutofillResult,
    FakePage,
    FormField,
    Page,
)
from jobhunt.autofill.generic import GenericAutofiller
from jobhunt.autofill.icims import IcimsAutofiller
from jobhunt.autofill.mapper import map_profile_to_fields
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
    "WorkdayAutofiller",
    "map_profile_to_fields",
]
