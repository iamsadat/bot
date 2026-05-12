"""Job source adapters.

Each adapter implements the JobSource protocol. The MVP ships a fixture
adapter (offline, deterministic) so tests run without network access.
Real adapters (Greenhouse, Lever, Ashby, Indeed RSS) plug into the same
interface in Phase 1.
"""

from jobhunt.adapters.base import JobSource, SourceUnavailable
from jobhunt.adapters.fixture import FixtureSource

__all__ = ["JobSource", "SourceUnavailable", "FixtureSource"]
