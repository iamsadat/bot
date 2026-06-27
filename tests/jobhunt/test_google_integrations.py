"""Tests for jobhunt.integrations — Gmail + Calendar Google API integration.

Fully offline: all HTTP is intercepted by ``FakeTransport`` so the suite
runs with no real OAuth and no network access whatsoever.
"""

from __future__ import annotations

import base64
import time

import pytest

from jobhunt.inbox.sources import InboxMessage
from jobhunt.integrations import (
    CalendarEvent,
    FakeTransport,
    GoogleAPIError,
    GoogleCalendarClient,
    GoogleCredentials,
    GmailInboxSource,
    OAuthTokenProvider,
    StaticTokenProvider,
)
from jobhunt.integrations.gmail import (
    GMAIL_API_BASE,
    _b64url_decode,
    _company_from_email,
    _extract_body_from_payload,
)
from jobhunt.integrations.gcal import CALENDAR_API_BASE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _make_credentials(
    access_token: str = "old-token",
    refresh_token: str = "refresh-xyz",
    expiry_epoch: float = 1000.0,
    client_id: str = "client-123",
    client_secret: str = "secret-abc",
) -> GoogleCredentials:
    return GoogleCredentials(
        access_token=access_token,
        refresh_token=refresh_token,
        expiry_epoch=expiry_epoch,
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )


def _gmail_message_payload(
    msg_id: str = "m1",
    subject: str = "Interview Invitation",
    sender: str = "hr@stripe.com",
    date: str = "Mon, 1 Jan 2024 10:00:00 +0000",
    body_text: str = "Let's schedule your interview.",
    internal_date_ms: str | None = "1704103200000",
) -> dict:
    payload = {
        "id": msg_id,
        "snippet": body_text[:50],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "Date", "value": date},
            ],
            "body": {"data": _b64url(body_text)},
        },
    }
    if internal_date_ms is not None:
        payload["internalDate"] = internal_date_ms
    return payload


def _gmail_multipart_payload(
    msg_id: str = "m2",
    subject: str = "Multipart message",
    sender: str = "jobs@acme.io",
    plain_text: str = "Plain body here.",
    html_text: str = "<html><body><b>HTML</b> body here.</body></html>",
) -> dict:
    return {
        "id": msg_id,
        "snippet": plain_text[:50],
        "internalDate": "1704103200000",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64url(plain_text)},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64url(html_text)},
                },
            ],
        },
    }


# ===========================================================================
# FakeTransport
# ===========================================================================

class TestFakeTransport:
    def test_records_calls_in_order(self):
        t = FakeTransport()
        t.add("GET", "/a", 200, {"ok": 1})
        t.add("POST", "/b", 200, {"ok": 2})

        t.request("GET", "https://x.test/a")
        t.request("POST", "https://x.test/b", body={"k": "v"})

        assert len(t.calls) == 2
        assert t.calls[0]["method"] == "GET"
        assert t.calls[0]["url"] == "https://x.test/a"
        assert t.calls[1]["method"] == "POST"
        assert t.calls[1]["body"] == {"k": "v"}

    def test_matches_by_url_substring(self):
        t = FakeTransport()
        t.add("GET", "/messages/m1", 200, {"id": "m1"})
        status, payload = t.request("GET", "https://gmail.test/v1/users/me/messages/m1?format=full")
        assert status == 200
        assert payload == {"id": "m1"}

    def test_unmapped_route_raises_google_api_error(self):
        t = FakeTransport()
        with pytest.raises(GoogleAPIError):
            t.request("GET", "https://nowhere.test/x")

    def test_status_over_400_raises(self):
        t = FakeTransport()
        t.add("GET", "/fail", 404, {"error": "not found"})
        with pytest.raises(GoogleAPIError):
            t.request("GET", "https://x.test/fail")

    def test_status_500_raises(self):
        t = FakeTransport()
        t.add("POST", "/boom", 500, {"error": "server error"})
        with pytest.raises(GoogleAPIError):
            t.request("POST", "https://x.test/boom")

    def test_callable_response_is_invoked(self):
        calls = {"n": 0}

        def dynamic():
            calls["n"] += 1
            return (200, {"count": calls["n"]})

        t = FakeTransport()
        t.add("GET", "/dyn", 200, {})  # overwritten below
        t._routes[("GET", "/dyn")] = dynamic
        status, payload = t.request("GET", "https://x.test/dyn")
        assert status == 200
        assert payload == {"count": 1}
        status, payload = t.request("GET", "https://x.test/dyn")
        assert payload == {"count": 2}

    def test_method_must_match(self):
        t = FakeTransport()
        t.add("GET", "/x", 200, {"ok": True})
        with pytest.raises(GoogleAPIError):
            t.request("POST", "https://x.test/x")

    def test_headers_recorded(self):
        t = FakeTransport()
        t.add("GET", "/x", 200, {})
        t.request("GET", "https://x.test/x", headers={"Authorization": "Bearer tok"})
        assert t.calls[0]["headers"] == {"Authorization": "Bearer tok"}

    def test_constructed_with_routes_dict(self):
        t = FakeTransport(routes={("GET", "/seed"): (200, {"seeded": True})})
        status, payload = t.request("GET", "https://x.test/seed")
        assert status == 200
        assert payload == {"seeded": True}


# ===========================================================================
# StaticTokenProvider
# ===========================================================================

class TestStaticTokenProvider:
    def test_returns_fixed_token(self):
        provider = StaticTokenProvider("abc123")
        assert provider.get_access_token() == "abc123"
        assert provider.get_access_token() == "abc123"

    def test_default_token(self):
        provider = StaticTokenProvider()
        assert provider.get_access_token() == "fake-access-token"


# ===========================================================================
# OAuthTokenProvider
# ===========================================================================

class TestOAuthTokenProvider:
    def test_refreshes_when_expired(self):
        creds = _make_credentials(access_token="stale", expiry_epoch=1000.0)
        transport = FakeTransport()
        transport.add(
            "POST", "oauth2.googleapis.com/token", 200,
            {"access_token": "fresh-token", "expires_in": 3600},
        )
        clock_value = [1000.0]  # at/after expiry -> must refresh
        provider = OAuthTokenProvider(creds, transport, clock=lambda: clock_value[0])

        token = provider.get_access_token()

        assert token == "fresh-token"
        assert creds.access_token == "fresh-token"
        assert creds.expiry_epoch == 1000.0 + 3600
        assert len(transport.calls) == 1

    def test_reuses_cached_token_when_valid(self):
        creds = _make_credentials(access_token="still-valid", expiry_epoch=10_000.0)
        transport = FakeTransport()
        transport.add("POST", "oauth2.googleapis.com/token", 200, {"access_token": "should-not-be-used"})
        provider = OAuthTokenProvider(creds, transport, clock=lambda: 100.0)

        token1 = provider.get_access_token()
        token2 = provider.get_access_token()

        assert token1 == "still-valid"
        assert token2 == "still-valid"
        assert transport.calls == []  # never refreshed

    def test_raises_on_refresh_failure_status(self):
        creds = _make_credentials(expiry_epoch=0.0)
        transport = FakeTransport()
        transport.add(
            "POST", "oauth2.googleapis.com/token", 401,
            {"error": "invalid_grant"},
        )
        provider = OAuthTokenProvider(creds, transport, clock=lambda: 0.0)
        with pytest.raises(GoogleAPIError):
            provider.get_access_token()

    def test_raises_when_response_missing_access_token(self):
        creds = _make_credentials(expiry_epoch=0.0)
        transport = FakeTransport()
        transport.add("POST", "oauth2.googleapis.com/token", 200, {"expires_in": 10})
        provider = OAuthTokenProvider(creds, transport, clock=lambda: 0.0)
        with pytest.raises(GoogleAPIError):
            provider.get_access_token()

    def test_expiry_margin_triggers_early_refresh(self):
        # expiry_epoch is 20s in the future from clock, well within the
        # 30s safety margin -> should still trigger a refresh.
        creds = _make_credentials(access_token="about-to-expire", expiry_epoch=120.0)
        transport = FakeTransport()
        transport.add(
            "POST", "oauth2.googleapis.com/token", 200,
            {"access_token": "renewed", "expires_in": 3600},
        )
        provider = OAuthTokenProvider(creds, transport, clock=lambda: 100.0)
        token = provider.get_access_token()
        assert token == "renewed"

    def test_clock_driven_refresh_then_reuse(self):
        creds = _make_credentials(access_token="t0", expiry_epoch=1000.0)
        transport = FakeTransport()
        transport.add(
            "POST", "oauth2.googleapis.com/token", 200,
            {"access_token": "t1", "expires_in": 500},
        )
        clock_value = [1000.0]
        provider = OAuthTokenProvider(creds, transport, clock=lambda: clock_value[0])

        token1 = provider.get_access_token()
        assert token1 == "t1"
        assert len(transport.calls) == 1

        # Move the clock forward but stay within the new expiry window.
        clock_value[0] = 1100.0
        token2 = provider.get_access_token()
        assert token2 == "t1"
        assert len(transport.calls) == 1  # no second refresh

    def test_default_expires_in_used_when_absent(self):
        creds = _make_credentials(expiry_epoch=0.0)
        transport = FakeTransport()
        transport.add("POST", "oauth2.googleapis.com/token", 200, {"access_token": "tok"})
        provider = OAuthTokenProvider(creds, transport, clock=lambda: 0.0)
        provider.get_access_token()
        assert creds.expiry_epoch == 0.0 + 3600  # default expires_in

    def test_refresh_request_body_shape(self):
        creds = _make_credentials(
            refresh_token="rt-1", client_id="cid-1", client_secret="cs-1", expiry_epoch=0.0,
        )
        transport = FakeTransport()
        transport.add(
            "POST", "oauth2.googleapis.com/token", 200,
            {"access_token": "tok", "expires_in": 60},
        )
        provider = OAuthTokenProvider(creds, transport, clock=lambda: 0.0)
        provider.get_access_token()

        call = transport.calls[0]
        assert call["method"] == "POST"
        assert call["body"]["grant_type"] == "refresh_token"
        assert call["body"]["refresh_token"] == "rt-1"
        assert call["body"]["client_id"] == "cid-1"
        assert call["body"]["client_secret"] == "cs-1"


# ===========================================================================
# Company-from-sender derivation
# ===========================================================================

class TestCompanyFromEmail:
    def test_basic_domain(self):
        assert _company_from_email("noreply@stripe.com") == "Stripe"

    def test_subdomain_noise_stripped(self):
        assert _company_from_email("jobs@careers.acme-corp.io") == "Acme Corp"

    def test_reexported_helper_is_same_function(self):
        from jobhunt.inbox.sources import _company_from_email as direct
        assert _company_from_email is direct


# ===========================================================================
# Base64url decoding helper
# ===========================================================================

class TestB64UrlDecode:
    def test_decodes_padded_string(self):
        encoded = base64.urlsafe_b64encode(b"hello world").decode()
        assert _b64url_decode(encoded) == b"hello world"

    def test_decodes_unpadded_string_gmail_style(self):
        encoded = base64.urlsafe_b64encode(b"hello world").decode().rstrip("=")
        assert _b64url_decode(encoded) == b"hello world"

    def test_empty_string_returns_empty_bytes(self):
        assert _b64url_decode("") == b""

    def test_garbage_input_does_not_raise(self):
        # urlsafe_b64decode is lenient about its alphabet; the important
        # contract is that malformed input never raises, it just decodes
        # to *something* (or empty bytes).
        result = _b64url_decode("%%%not-valid-base64%%%")
        assert isinstance(result, bytes)


# ===========================================================================
# _extract_body_from_payload
# ===========================================================================

class TestExtractBodyFromPayload:
    def test_single_part_plain_text(self):
        payload = {
            "mimeType": "text/plain",
            "body": {"data": _b64url("hello plain")},
        }
        assert _extract_body_from_payload(payload) == "hello plain"

    def test_multipart_prefers_plain_over_html(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/html", "body": {"data": _b64url("<p>hi</p>")}},
                {"mimeType": "text/plain", "body": {"data": _b64url("hi plain")}},
            ],
        }
        assert _extract_body_from_payload(payload) == "hi plain"

    def test_html_only_falls_back_with_tags_stripped(self):
        payload = {
            "mimeType": "text/html",
            "body": {"data": _b64url("<html><body>Hello <b>there</b></body></html>")},
        }
        result = _extract_body_from_payload(payload)
        assert "<" not in result
        assert "Hello" in result

    def test_no_body_returns_empty_string(self):
        assert _extract_body_from_payload({"mimeType": "text/plain", "body": {}}) == ""

    def test_nested_multipart_mixed(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": _b64url("nested plain")}},
                    ],
                },
            ],
        }
        assert _extract_body_from_payload(payload) == "nested plain"


# ===========================================================================
# GmailInboxSource — fetch / list / parse
# ===========================================================================

class TestGmailInboxSourceFetch:
    def _source(self, transport: FakeTransport) -> GmailInboxSource:
        return GmailInboxSource(StaticTokenProvider("tok-123"), transport)

    def test_fetch_lists_and_parses_messages(self):
        transport = FakeTransport()
        transport.add(
            "GET", "/messages?", 200,
            {"messages": [{"id": "m1"}, {"id": "m2"}]},
        )
        transport.add("GET", "/messages/m1", 200, _gmail_message_payload(msg_id="m1"))
        transport.add(
            "GET", "/messages/m2", 200,
            _gmail_message_payload(msg_id="m2", subject="Rejection", sender="hr@acme.com"),
        )

        source = self._source(transport)
        result = source.fetch()

        assert len(result) == 2
        assert all(isinstance(m, InboxMessage) for m in result)
        assert result[0].message_id == "m1"
        assert result[0].subject == "Interview Invitation"
        assert result[0].sender == "hr@stripe.com"
        assert result[0].company == "Stripe"
        assert result[0].body == "Let's schedule your interview."

    def test_fetch_empty_inbox_returns_empty_list(self):
        transport = FakeTransport()
        transport.add("GET", "/messages?", 200, {"messages": []})
        source = self._source(transport)
        result = source.fetch()
        assert result == []

    def test_fetch_missing_messages_key_returns_empty_list(self):
        transport = FakeTransport()
        transport.add("GET", "/messages?", 200, {})
        source = self._source(transport)
        assert source.fetch() == []

    def test_fetch_respects_since_filter(self):
        transport = FakeTransport()
        transport.add("GET", "/messages?", 200, {"messages": [{"id": "old"}, {"id": "new"}]})
        transport.add(
            "GET", "/messages/old", 200,
            _gmail_message_payload(msg_id="old", internal_date_ms="1000000"),
        )
        transport.add(
            "GET", "/messages/new", 200,
            _gmail_message_payload(msg_id="new", internal_date_ms="9000000000"),
        )
        source = self._source(transport)
        result = source.fetch(since=5000.0)
        assert [m.message_id for m in result] == ["new"]

    def test_fetch_respects_max_messages(self):
        transport = FakeTransport()
        transport.add(
            "GET", "/messages?", 200,
            {"messages": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]},
        )
        for mid in ("m1", "m2", "m3"):
            transport.add("GET", f"/messages/{mid}", 200, _gmail_message_payload(msg_id=mid))
        source = self._source(transport)
        result = source.fetch(max_messages=2)
        assert len(result) == 2

    def test_fetch_decodes_multipart_message(self):
        transport = FakeTransport()
        transport.add("GET", "/messages?", 200, {"messages": [{"id": "m2"}]})
        transport.add("GET", "/messages/m2", 200, _gmail_multipart_payload())
        source = self._source(transport)
        result = source.fetch()
        assert len(result) == 1
        assert result[0].body == "Plain body here."
        assert result[0].company == "Acme"

    def test_fetch_falls_back_to_snippet_when_no_body(self):
        payload = _gmail_message_payload(msg_id="m9")
        payload["payload"]["body"] = {}
        transport = FakeTransport()
        transport.add("GET", "/messages?", 200, {"messages": [{"id": "m9"}]})
        transport.add("GET", "/messages/m9", 200, payload)
        source = self._source(transport)
        result = source.fetch()
        assert result[0].body == payload["snippet"]

    def test_fetch_uses_internal_date_for_received_at(self):
        transport = FakeTransport()
        transport.add("GET", "/messages?", 200, {"messages": [{"id": "m1"}]})
        transport.add(
            "GET", "/messages/m1", 200,
            _gmail_message_payload(msg_id="m1", internal_date_ms="1704103200000"),
        )
        source = self._source(transport)
        result = source.fetch()
        assert result[0].received_at == pytest.approx(1704103200.0)

    def test_fetch_falls_back_to_date_header_when_no_internal_date(self):
        transport = FakeTransport()
        transport.add("GET", "/messages?", 200, {"messages": [{"id": "m1"}]})
        transport.add(
            "GET", "/messages/m1", 200,
            _gmail_message_payload(
                msg_id="m1", date="Mon, 1 Jan 2024 10:00:00 +0000", internal_date_ms=None
            ),
        )
        source = self._source(transport)
        result = source.fetch()
        assert result[0].received_at > 0

    def test_source_has_name_attribute(self):
        transport = FakeTransport()
        source = self._source(transport)
        assert source.name == "gmail"

    def test_fetch_sends_bearer_token_header(self):
        transport = FakeTransport()
        transport.add("GET", "/messages?", 200, {"messages": []})
        source = GmailInboxSource(StaticTokenProvider("secret-tok"), transport)
        source.fetch()
        assert transport.calls[0]["headers"]["Authorization"] == "Bearer secret-tok"

    def test_fetch_list_url_includes_label_ids(self):
        transport = FakeTransport()
        transport.add("GET", "/messages?", 200, {"messages": []})
        source = GmailInboxSource(
            StaticTokenProvider("tok"), transport, label_ids=["INBOX", "IMPORTANT"],
        )
        source.fetch()
        url = transport.calls[0]["url"]
        assert "labelIds=INBOX" in url
        assert "labelIds=IMPORTANT" in url

    def test_to_dict_compatible_with_tracking_inputs_shape(self):
        transport = FakeTransport()
        transport.add("GET", "/messages?", 200, {"messages": [{"id": "m1"}]})
        transport.add("GET", "/messages/m1", 200, _gmail_message_payload(msg_id="m1"))
        source = self._source(transport)
        result = source.fetch()
        d = result[0].to_dict()
        assert set(d.keys()) == {"subject", "body", "company"}


# ===========================================================================
# GmailInboxSource — mark_read
# ===========================================================================

class TestGmailInboxSourceMarkRead:
    def test_mark_read_calls_modify_endpoint(self):
        transport = FakeTransport()
        transport.add("POST", "/messages/m1/modify", 200, {"id": "m1", "labelIds": []})
        source = GmailInboxSource(StaticTokenProvider("tok"), transport)

        source.mark_read("m1")

        assert len(transport.calls) == 1
        call = transport.calls[0]
        assert call["method"] == "POST"
        assert "/messages/m1/modify" in call["url"]
        assert call["body"] == {"removeLabelIds": ["UNREAD"]}

    def test_mark_read_sends_bearer_token(self):
        transport = FakeTransport()
        transport.add("POST", "/messages/m1/modify", 200, {})
        source = GmailInboxSource(StaticTokenProvider("tok-xyz"), transport)
        source.mark_read("m1")
        assert transport.calls[0]["headers"]["Authorization"] == "Bearer tok-xyz"

    def test_mark_read_unmapped_raises(self):
        transport = FakeTransport()
        source = GmailInboxSource(StaticTokenProvider("tok"), transport)
        with pytest.raises(GoogleAPIError):
            source.mark_read("missing-msg")


# ===========================================================================
# GoogleCalendarClient — list_events
# ===========================================================================

class TestGoogleCalendarClientListEvents:
    def _client(self, transport: FakeTransport) -> GoogleCalendarClient:
        return GoogleCalendarClient(StaticTokenProvider("tok"), transport)

    def test_list_events_parses_results(self):
        transport = FakeTransport()
        transport.add(
            "GET", "calendars/primary/events", 200,
            {
                "items": [
                    {
                        "id": "evt-1",
                        "summary": "Interview with Acme",
                        "start": {"dateTime": "2024-03-04T14:00:00-05:00"},
                        "end": {"dateTime": "2024-03-04T15:00:00-05:00"},
                        "htmlLink": "https://calendar.google.com/event?eid=abc",
                        "hangoutLink": "https://meet.google.com/abc-defg-hij",
                    },
                ],
            },
        )
        client = self._client(transport)
        events = client.list_events("2024-03-01T00:00:00Z", "2024-03-31T00:00:00Z")

        assert len(events) == 1
        evt = events[0]
        assert isinstance(evt, CalendarEvent)
        assert evt.id == "evt-1"
        assert evt.summary == "Interview with Acme"
        assert evt.start == "2024-03-04T14:00:00-05:00"
        assert evt.end == "2024-03-04T15:00:00-05:00"
        assert evt.html_link == "https://calendar.google.com/event?eid=abc"
        assert evt.hangout_link == "https://meet.google.com/abc-defg-hij"

    def test_list_events_empty_returns_empty_list(self):
        transport = FakeTransport()
        transport.add("GET", "calendars/primary/events", 200, {"items": []})
        client = self._client(transport)
        assert client.list_events("2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z") == []

    def test_list_events_missing_items_key_returns_empty_list(self):
        transport = FakeTransport()
        transport.add("GET", "calendars/primary/events", 200, {})
        client = self._client(transport)
        assert client.list_events("2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z") == []

    def test_list_events_sends_time_range_query_params(self):
        transport = FakeTransport()
        transport.add("GET", "calendars/primary/events", 200, {"items": []})
        client = self._client(transport)
        client.list_events("2024-03-01T00:00:00Z", "2024-03-31T00:00:00Z")
        url = transport.calls[0]["url"]
        assert "timeMin=2024-03-01T00:00:00Z" in url
        assert "timeMax=2024-03-31T00:00:00Z" in url

    def test_list_events_all_day_event_uses_date_field(self):
        transport = FakeTransport()
        transport.add(
            "GET", "calendars/primary/events", 200,
            {
                "items": [
                    {
                        "id": "evt-2",
                        "summary": "All-day hold",
                        "start": {"date": "2024-03-04"},
                        "end": {"date": "2024-03-05"},
                    },
                ],
            },
        )
        client = self._client(transport)
        events = client.list_events("2024-03-01T00:00:00Z", "2024-03-31T00:00:00Z")
        assert events[0].start == "2024-03-04"
        assert events[0].end == "2024-03-05"

    def test_list_events_error_status_raises(self):
        transport = FakeTransport()
        transport.add("GET", "calendars/primary/events", 403, {"error": "forbidden"})
        client = self._client(transport)
        with pytest.raises(GoogleAPIError):
            client.list_events("2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z")


# ===========================================================================
# GoogleCalendarClient — create_event
# ===========================================================================

class TestGoogleCalendarClientCreateEvent:
    def _client(self, transport: FakeTransport) -> GoogleCalendarClient:
        return GoogleCalendarClient(StaticTokenProvider("tok"), transport)

    def test_create_event_sends_correct_json_body(self):
        transport = FakeTransport()
        transport.add(
            "POST", "calendars/primary/events", 200,
            {
                "id": "evt-new",
                "summary": "Interview: Backend Engineer",
                "start": {"dateTime": "2024-03-04T14:00:00-05:00"},
                "end": {"dateTime": "2024-03-04T15:00:00-05:00"},
                "htmlLink": "https://calendar.google.com/event?eid=new",
            },
        )
        client = self._client(transport)
        event = client.create_event(
            summary="Interview: Backend Engineer",
            start_iso="2024-03-04T14:00:00-05:00",
            end_iso="2024-03-04T15:00:00-05:00",
            description="Phone screen with Acme recruiting.",
            attendees=["candidate@example.com", "recruiter@acme.com"],
        )

        assert isinstance(event, CalendarEvent)
        assert event.id == "evt-new"
        assert event.summary == "Interview: Backend Engineer"
        assert event.html_link == "https://calendar.google.com/event?eid=new"

        call = transport.calls[0]
        assert call["method"] == "POST"
        assert call["body"]["summary"] == "Interview: Backend Engineer"
        assert call["body"]["description"] == "Phone screen with Acme recruiting."
        assert call["body"]["start"] == {"dateTime": "2024-03-04T14:00:00-05:00"}
        assert call["body"]["end"] == {"dateTime": "2024-03-04T15:00:00-05:00"}
        assert call["body"]["attendees"] == [
            {"email": "candidate@example.com"}, {"email": "recruiter@acme.com"},
        ]

    def test_create_event_without_attendees_omits_field(self):
        transport = FakeTransport()
        transport.add(
            "POST", "calendars/primary/events", 200,
            {"id": "evt-x", "summary": "Hold", "start": {}, "end": {}},
        )
        client = self._client(transport)
        client.create_event("Hold", "2024-01-01T10:00:00Z", "2024-01-01T11:00:00Z")
        call = transport.calls[0]
        assert "attendees" not in call["body"]

    def test_create_event_default_description_is_empty(self):
        transport = FakeTransport()
        transport.add(
            "POST", "calendars/primary/events", 200,
            {"id": "evt-y", "summary": "Hold", "start": {}, "end": {}},
        )
        client = self._client(transport)
        client.create_event("Hold", "2024-01-01T10:00:00Z", "2024-01-01T11:00:00Z")
        assert transport.calls[0]["body"]["description"] == ""

    def test_create_event_error_on_4xx(self):
        transport = FakeTransport()
        transport.add("POST", "calendars/primary/events", 400, {"error": "bad request"})
        client = self._client(transport)
        with pytest.raises(GoogleAPIError):
            client.create_event("Bad", "not-a-date", "also-not-a-date")

    def test_create_event_sends_bearer_token(self):
        transport = FakeTransport()
        transport.add(
            "POST", "calendars/primary/events", 200,
            {"id": "evt-z", "summary": "Hold", "start": {}, "end": {}},
        )
        client = GoogleCalendarClient(StaticTokenProvider("cal-tok"), transport)
        client.create_event("Hold", "2024-01-01T10:00:00Z", "2024-01-01T11:00:00Z")
        assert transport.calls[0]["headers"]["Authorization"] == "Bearer cal-tok"


# ===========================================================================
# End-to-end: OAuthTokenProvider feeding GmailInboxSource + GoogleCalendarClient
# ===========================================================================

class TestEndToEndWithOAuthTokenProvider:
    def test_gmail_source_uses_refreshed_token(self):
        creds = _make_credentials(access_token="expired", expiry_epoch=0.0)
        transport = FakeTransport()
        transport.add(
            "POST", "oauth2.googleapis.com/token", 200,
            {"access_token": "brand-new-token", "expires_in": 3600},
        )
        transport.add("GET", "/messages?", 200, {"messages": []})

        provider = OAuthTokenProvider(creds, transport, clock=lambda: 0.0)
        source = GmailInboxSource(provider, transport)
        source.fetch()

        # First call is the token refresh, second is the Gmail list call
        # carrying the freshly refreshed bearer token.
        assert transport.calls[0]["url"] == "https://oauth2.googleapis.com/token"
        assert transport.calls[1]["headers"]["Authorization"] == "Bearer brand-new-token"

    def test_calendar_client_uses_refreshed_token(self):
        creds = _make_credentials(access_token="expired", expiry_epoch=0.0)
        transport = FakeTransport()
        transport.add(
            "POST", "oauth2.googleapis.com/token", 200,
            {"access_token": "cal-fresh-token", "expires_in": 3600},
        )
        transport.add("GET", "calendars/primary/events", 200, {"items": []})

        provider = OAuthTokenProvider(creds, transport, clock=lambda: 0.0)
        client = GoogleCalendarClient(provider, transport)
        client.list_events("2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z")

        assert transport.calls[1]["headers"]["Authorization"] == "Bearer cal-fresh-token"
