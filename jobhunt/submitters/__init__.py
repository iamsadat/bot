"""jobhunt.submitters — ATS auto-submit layer (Phase 3)."""

from jobhunt.submitters.base import FakePoster, Poster, SubmitResult, Submitter, UrllibPoster
from jobhunt.submitters.greenhouse import GreenhouseSubmitter
from jobhunt.submitters.lever import LeverSubmitter
from jobhunt.submitters.registry import SubmitterRegistry

__all__ = [
    "FakePoster",
    "GreenhouseSubmitter",
    "LeverSubmitter",
    "Poster",
    "SubmitResult",
    "Submitter",
    "SubmitterRegistry",
    "UrllibPoster",
]
