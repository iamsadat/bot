"""Module-level ASGI app for production hosting.

Lets any ASGI server import a ready-to-serve app without the CLI:

    uvicorn jobhunt.dashboard.app:app --host 0.0.0.0 --port 8000

This is what the Dockerfile / render.yaml run. The app is multi-tenant:
each browser gets its own cookie-isolated ``DashboardState``, backed by its
own SQLite file under ``JOBHUNT_WORKSPACES_DIR`` (so ~10 concurrent testers
don't share or clobber each other's jobs/applications). State is restored
per-workspace on first access after a redeploy.

Env vars:
  JOBHUNT_WORKSPACES_DIR   directory for per-workspace SQLite files
                           (default "workspaces", relative to cwd)
  JOBHUNT_WORKSPACE_CAP    max number of workspaces kept warm in memory,
                           LRU-evicted beyond this (default 200)
  JOBHUNT_ACCESS_CODE      if set, gates /api/* and /ws/stream behind a
                           shared X-Access-Code header / ?code= param
                           (default unset = gate off, fully open)
  JOBHUNT_DEV_NAV          if truthy ("1", "true", "yes"), shows the dev-only
                           Tracker/Demo links in the header. Off by default so
                           production builds don't surface them.
  JOBHUNT_PERSONAL         if truthy, run as one owner's tool instead: a single
                           state persisted to JOBHUNT_DB_PATH / DATABASE_URL,
                           with the background sweeps, digests and
                           notifications that per-browser workspaces cannot
                           run. See jobhunt/personal.py.
"""

from __future__ import annotations

import os
from pathlib import Path

from jobhunt.dashboard.persistence import DashboardStore
from jobhunt.dashboard.server import DashboardState, WorkspaceManager, create_app
from jobhunt.personal import enable, is_personal, seed_profile
from jobhunt.trace import ThoughtBus, TraceStore

if is_personal():  # load me.env before anything below reads the environment
    enable(os.environ.get("JOBHUNT_ENV_FILE", "me.env"))

_workspaces_dir = Path(os.environ.get("JOBHUNT_WORKSPACES_DIR", "workspaces"))
_workspace_cap = int(os.environ.get("JOBHUNT_WORKSPACE_CAP", "200"))
_access_code = os.environ.get("JOBHUNT_ACCESS_CODE") or None
_dev_nav = os.environ.get("JOBHUNT_DEV_NAV", "").strip().lower() in ("1", "true", "yes", "on")

if is_personal():
    _state = DashboardState(
        trace_store=TraceStore(), bus=ThoughtBus(),
        store=DashboardStore(os.environ.get("JOBHUNT_DB_PATH", "jobhunt.db"),
                             db_url=os.environ.get("DATABASE_URL") or None),
    )
    _state.restore()
    seed_profile(_state)
    app = create_app(_state, access_code=_access_code, dev_nav=_dev_nav)
else:
    _manager = WorkspaceManager(base_dir=_workspaces_dir, cap=_workspace_cap)

    # The ASGI application object servers look for.
    app = create_app(
        workspace_factory=_manager.get, access_code=_access_code, dev_nav=_dev_nav,
    )
