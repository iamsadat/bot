"""Google Calendar REST integration.

``GoogleCalendarClient`` lets the Tracking agent read upcoming events and
turn a detected interview into a real calendar hold, without any Google
client libraries or real network access — all calls go through an
injectable :class:`~jobhunt.integrations.google_auth.Transport`.
"""

from __future__ import annotations

from dataclasses import dataclass

from jobhunt.integrations.google_auth import TokenProvider, Transport

CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CalendarEvent:
    """Canonical representation of one calendar event."""

    id: str
    summary: str
    start: str                  # ISO 8601 datetime string
    end: str                    # ISO 8601 datetime string
    html_link: str = ""
    hangout_link: str = ""
    location: str = ""


def _event_from_json(payload: dict) -> CalendarEvent:
    start = payload.get("start", {}) or {}
    end = payload.get("end", {}) or {}
    return CalendarEvent(
        id=payload.get("id", ""),
        summary=payload.get("summary", "") or "",
        start=start.get("dateTime", start.get("date", "")) or "",
        end=end.get("dateTime", end.get("date", "")) or "",
        html_link=payload.get("htmlLink", "") or "",
        hangout_link=payload.get("hangoutLink", "") or "",
        location=payload.get("location", "") or "",
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class GoogleCalendarClient:
    """Google Calendar (primary calendar) client.

    Constructed with a :class:`TokenProvider` and :class:`Transport`; makes
    no network calls until ``list_events``/``create_event`` are invoked.
    """

    def __init__(
        self,
        token_provider: TokenProvider,
        transport: Transport,
        *,
        events_url: str = CALENDAR_API_BASE,
    ) -> None:
        self._tokens = token_provider
        self._transport = transport
        self._events_url = events_url

    def list_events(self, time_min: str, time_max: str) -> list[CalendarEvent]:
        """List events on the primary calendar within ``[time_min, time_max]``.

        ``time_min``/``time_max`` are ISO 8601 datetime strings (Google's
        ``timeMin``/``timeMax`` query params).
        """
        url = (
            f"{self._events_url}?timeMin={time_min}&timeMax={time_max}"
            "&singleEvents=true&orderBy=startTime"
        )
        _, payload = self._request("GET", url)
        items = payload.get("items", []) or []
        return [_event_from_json(item) for item in items]

    def create_event(
        self,
        summary: str,
        start_iso: str,
        end_iso: str,
        description: str = "",
        attendees: list[str] | None = None,
    ) -> CalendarEvent:
        """Create a calendar hold and return the resulting :class:`CalendarEvent`."""
        body: dict = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_iso},
            "end": {"dateTime": end_iso},
        }
        if attendees:
            body["attendees"] = [{"email": email} for email in attendees]

        _, payload = self._request("POST", self._events_url, body=body)
        return _event_from_json(payload)

    # ------------------------------------------------------------------

    def _request(
        self, method: str, url: str, *, body: dict | None = None
    ) -> tuple[int, dict]:
        token = self._tokens.get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        return self._transport.request(method, url, headers=headers, body=body)
