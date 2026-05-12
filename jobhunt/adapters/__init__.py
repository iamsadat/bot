"""Job source adapters.

Adapters share the :class:`JobSource` protocol from ``base``. The
package ships:

* :class:`FixtureSource` — offline, deterministic source used by the
  demo CLI and the orchestrator-level tests.
* :class:`GreenhouseSource`, :class:`LeverSource`, :class:`AshbySource` —
  real public-API adapters. They accept an injectable HTTP client so
  their tests stay offline by pointing at recorded JSON fixtures.
"""

from jobhunt.adapters.ashby import AshbySource
from jobhunt.adapters.base import JobSource, SourceUnavailable
from jobhunt.adapters.fixture import FixtureSource
from jobhunt.adapters.greenhouse import GreenhouseSource
from jobhunt.adapters.lever import LeverSource

__all__ = [
    "AshbySource",
    "FixtureSource",
    "GreenhouseSource",
    "JobSource",
    "LeverSource",
    "SourceUnavailable",
]
