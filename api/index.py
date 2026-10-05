"""Vercel entrypoint: the personal dashboard as one serverless function.

vercel.json rewrites /api/* and /ws/* here; the static frontend is served from
frontend/out by Vercel's CDN. State lives in Postgres (DATABASE_URL), shared
with `python -m jobhunt me` on a laptop — see jobhunt/personal.py.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JOBHUNT_PERSONAL", "1")
if not os.environ.get("DATABASE_URL"):
    os.environ.setdefault("JOBHUNT_DB_PATH", "/tmp/jobhunt.db")  # only /tmp is writable


def _report_unreachable_database() -> None:
    # Vercel's log view truncates long tracebacks from the bottom, which is
    # exactly where a database error's cause is. Lead with it instead. (The
    # import below must stay at top level: Vercel finds `app` statically.)
    url = os.environ.get("DATABASE_URL")
    if not url:
        return
    try:
        import sqlalchemy

        sqlalchemy.create_engine(url).connect().close()
    except Exception as exc:
        print(f"jobhunt database unreachable: {getattr(exc, 'orig', None) or exc!r}"[:600],
              flush=True)


_report_unreachable_database()

from jobhunt.dashboard.app import app  # noqa: E402,F401
