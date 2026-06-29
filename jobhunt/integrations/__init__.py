"""jobhunt.integrations — Google Gmail + Calendar API integrations.

Fully unit-testable offline via an injectable HTTP ``Transport`` — no real
OAuth, no network. Re-exports everything callers need so
``from jobhunt.integrations import X`` works without knowing the internal
module layout.
"""

from jobhunt.integrations.google_auth import (
    FakeTransport,
    GoogleAPIError,
    GoogleCredentials,
    OAuthTokenProvider,
    StaticTokenProvider,
    TokenProvider,
    Transport,
    UrllibTransport,
)
from jobhunt.integrations.gmail import GmailInboxSource
from jobhunt.integrations.gcal import CalendarEvent, GoogleCalendarClient
from jobhunt.integrations.github import GitHubClient, GitHubError, repos_to_projects

__all__ = [
    "GitHubClient",
    "GitHubError",
    "repos_to_projects",
    "FakeTransport",
    "GoogleAPIError",
    "GoogleCredentials",
    "OAuthTokenProvider",
    "StaticTokenProvider",
    "TokenProvider",
    "Transport",
    "UrllibTransport",
    "GmailInboxSource",
    "CalendarEvent",
    "GoogleCalendarClient",
]
