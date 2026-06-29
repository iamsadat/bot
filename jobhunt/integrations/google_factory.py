"""Build Gmail / Calendar clients from env OAuth credentials.

Reads ``JOBHUNT_GOOGLE_CLIENT_ID``/``_SECRET``/``_REFRESH_TOKEN`` and returns
real clients (UrllibTransport) — or ``None`` when unconfigured, so every caller
degrades to "feature off". No network at construction time.
"""

from __future__ import annotations

import os

from jobhunt.integrations.gcal import GoogleCalendarClient
from jobhunt.integrations.gmail import GmailInboxSource, GmailSender
from jobhunt.integrations.google_auth import (
    GoogleCredentials, OAuthTokenProvider, UrllibTransport,
)

_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
]


def _credentials_from_env() -> GoogleCredentials | None:
    cid = os.environ.get("JOBHUNT_GOOGLE_CLIENT_ID")
    secret = os.environ.get("JOBHUNT_GOOGLE_CLIENT_SECRET")
    refresh = os.environ.get("JOBHUNT_GOOGLE_REFRESH_TOKEN")
    if not (cid and secret and refresh):
        return None
    return GoogleCredentials(
        access_token="", refresh_token=refresh, expiry_epoch=0.0,
        client_id=cid, client_secret=secret, scopes=_SCOPES,
    )


def _provider():
    creds = _credentials_from_env()
    if creds is None:
        return None
    return OAuthTokenProvider(creds, UrllibTransport())


def google_configured() -> bool:
    return _credentials_from_env() is not None


def build_gmail_source_from_env() -> GmailInboxSource | None:
    prov = _provider()
    return GmailInboxSource(prov, UrllibTransport()) if prov else None


def build_gmail_sender_from_env() -> GmailSender | None:
    prov = _provider()
    user = os.environ.get("JOBHUNT_GOOGLE_USER", "me")
    return GmailSender(prov, UrllibTransport(), from_addr=user) if prov else None


def build_calendar_from_env() -> GoogleCalendarClient | None:
    prov = _provider()
    return GoogleCalendarClient(prov, UrllibTransport()) if prov else None
