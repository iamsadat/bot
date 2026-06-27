"""Module-level ASGI app for production hosting.

Lets any ASGI server import a ready-to-serve app without the CLI:

    uvicorn jobhunt.dashboard.app:app --host 0.0.0.0 --port 8000

This is what the Dockerfile / render.yaml run. Persistence path comes from
the ``JOBHUNT_DB_PATH`` env var (defaults to ``jobhunt.db``). State is
restored on import so a redeploy keeps prior jobs/applications.
"""

from __future__ import annotations

import os

from jobhunt.dashboard.persistence import DashboardStore
from jobhunt.dashboard.server import DashboardState, create_app
from jobhunt.trace import ThoughtBus, TraceStore

_db_path = os.environ.get("JOBHUNT_DB_PATH", "jobhunt.db")

_state = DashboardState(
    trace_store=TraceStore(),
    bus=ThoughtBus(),
    store=DashboardStore(_db_path),
)
_state.restore()

# The ASGI application object servers look for.
app = create_app(_state)
