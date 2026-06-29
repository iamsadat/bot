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
from jobhunt.integrations.gmail import GmailInboxSource, GmailSender
from jobhunt.integrations.gcal import CalendarEvent, GoogleCalendarClient
from jobhunt.integrations.github import GitHubClient, GitHubError, repos_to_projects
from jobhunt.integrations.google_factory import (
    build_calendar_from_env, build_gmail_sender_from_env,
    build_gmail_source_from_env, google_configured,
)

__all__ = [
    "GitHubClient",
    "GitHubError",
    "repos_to_projects",
    "GmailSender",
    "build_calendar_from_env",
    "build_gmail_sender_from_env",
    "build_gmail_source_from_env",
    "google_configured",
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
