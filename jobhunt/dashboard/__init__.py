"""Dashboard package — FastAPI app + static client.

Importing this module is cheap; the FastAPI app is only built when
``create_app`` is called so the rest of the package works without the
``fastapi`` dependency installed.
"""

from jobhunt.dashboard.server import create_app

__all__ = ["create_app"]
