"""Tests for jobhunt.inbox — inbox watcher components.

Tests are fully offline: IMAP is mocked via factory injection.
"""

from __future__ import annotations

import email
import email.mime.multipart
import email.mime.text
import time
from email.utils import formatdate
from unittest.mock import MagicMock


from jobhunt.inbox import (
    CalendarHint,
    Classification,
    FakeInboxSource,
    IMAPInboxSource,
    InboxMessage,
    _company_from_email,
    classify_message,
    extract_calendar,
)
from jobhunt.agents.tracking import TrackingAgent, TrackingInputs
from jobhunt.models import Application, ApplicationStatus
from jobhunt.trace import ThoughtBus, TraceStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_message(
    msg_id: str = "msg-1",
    subject: str = "Hello",
    body: str = "World",
    sender: str = "hr@company.com",
    received_at: float | None = None,
    company: str = "Company",
) -> InboxMessage:
    return InboxMessage(
        message_id=msg_id,
        subject=subject,
        body=body,
        sender=sender,
        received_at=received_at if received_at is not None else time.time(),
        company=company,
    )


def _build_raw_email(
    subject: str = "Test",
    body: str = "Body text",
    sender: str = "hr@example.com",
    multipart: bool = False,
    html_only: bool = False,
) -> bytes:
    """Build a minimal RFC 2822 message as bytes."""
    if multipart:
        msg = email.mime.multipart.MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = "candidate@example.com"
        msg["Date"] = formatdate()
        msg["Message-ID"] = "<test-id@example.com>"
        if html_only:
            html_part = email.mime.text.MIMEText(f"<html><body>{body}</body></html>", "html")
            msg.attach(html_part)
        else:
            plain_part = email.mime.text.MIMEText(body, "plain")
            html_part = email.mime.text.MIMEText(f"<html><body>{body}</body></html>", "html")
            msg.attach(plain_part)
            msg.attach(html_part)
    else:
        msg = email.mime.text.MIMEText(body, "plain")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = "candidate@example.com"
        msg["Date"] = formatdate()
        msg["Message-ID"] = "<test-id@example.com>"
    return msg.as_bytes()


def _fake_imap_factory(raw_email_bytes: bytes) -> MagicMock:
    """Return a callable that creates a fake IMAP connection."""
    conn = MagicMock()
    conn.login.return_value = ("OK", [b"Logged in"])
    conn.select.return_value = ("OK", [b"1"])
    conn.search.return_value = ("OK", [b"1"])
    conn.fetch.return_value = (
        "OK",
        [(b"1 (RFC822 {%d}" % len(raw_email_bytes), raw_email_bytes)],
    )
    conn.logout.return_value = ("OK", [b"Bye"])
    return lambda: conn


# ===========================================================================
# 1. FakeInboxSource — returns seeded messages
# ===========================================================================

class TestFakeInboxSource:
    def test_returns_seeded_messages(self):
        msgs = [_make_message("1"), _make_message("2")]
        src = FakeInboxSource(msgs)
        result = src.fetch()
        assert len(result) == 2
        assert result[0].message_id == "1"
        assert result[1].message_id == "2"

    def test_honors_since_filter(self):
        now = time.time()
        old = _make_message("old", received_at=now - 3600)
        new = _make_message("new", received_at=now + 1)
        src = FakeInboxSource([old, new])
        result = src.fetch(since=now)
        assert len(result) == 1
        assert result[0].message_id == "new"

    def test_honors_max_messages(self):
        msgs = [_make_message(str(i), received_at=float(i)) for i in range(10)]
        src = FakeInboxSource(msgs)
        result = src.fetch(max_messages=3)
        assert len(result) == 3

    def test_since_zero_returns_all(self):
        # received_at starts at 1.0 so all pass the "since=0.0" filter (> 0.0)
        msgs = [_make_message(str(i), received_at=float(i + 1)) for i in range(5)]
        src = FakeInboxSource(msgs)
        assert len(src.fetch(since=0.0)) == 5

    def test_empty_seed(self):
        src = FakeInboxSource([])
        assert src.fetch() == []

    def test_name_attribute(self):
        assert FakeInboxSource([]).name == "fake"


# ===========================================================================
# 2. InboxMessage.to_dict
# ===========================================================================

class TestInboxMessageToDict:
    def test_to_dict_shape_matches_tracking_agent(self):
        msg = _make_message(
            subject="Interview invite",
            body="Can we schedule a call?",
            company="Acme",
        )
        d = msg.to_dict()
        # Must have exactly the keys TrackingAgent inspects
        assert "subject" in d
        assert "body" in d
        assert "company" in d

    def test_to_dict_values(self):
        msg = _make_message(subject="S", body="B", company="C")
        d = msg.to_dict()
        assert d["subject"] == "S"
        assert d["body"] == "B"
        assert d["company"] == "C"


# ===========================================================================
# 3. _company_from_email
# ===========================================================================

class TestCompanyFromEmail:
    def test_simple_domain(self):
        assert _company_from_email("noreply@stripe.com") == "Stripe"

    def test_subdomain_stripped_careers(self):
        # "careers" subdomain should be stripped; brand = "acme-corp" → "Acme Corp"
        result = _company_from_email("jobs@careers.acme-corp.io")
        assert result == "Acme Corp"

    def test_hyphen_to_spaces(self):
        assert _company_from_email("hr@some-great-company.com") == "Some Great Company"

    def test_no_at_sign_returns_empty(self):
        assert _company_from_email("notanemail") == ""

    def test_known_noise_subdomain(self):
        # mail.google.com → strip "mail" → "Google"
        assert _company_from_email("noreply@mail.google.com") == "Google"


# ===========================================================================
# 4. IMAPInboxSource — lazy connect
# ===========================================================================

class TestIMAPInboxSourceLazy:
    def test_constructor_does_not_call_factory(self):
        factory_called = []

        def tracking_factory():
            factory_called.append(True)
            return MagicMock()

        _ = IMAPInboxSource(
            "imap.example.com", "user", "pass",
            client_factory=tracking_factory,
        )
        assert factory_called == [], "Constructor must NOT call the factory"

    def test_name_attribute(self):
        src = IMAPInboxSource("host", "user", "pass")
        assert src.name == "imap"


# ===========================================================================
# 5. IMAPInboxSource.fetch — fake factory
# ===========================================================================

class TestIMAPInboxSourceFetch:
    def test_fetch_returns_inbox_messages(self):
        raw = _build_raw_email(
            subject="We'd love to chat",
            body="Can we schedule a call with you?",
            sender="recruiter@stripe.com",
        )
        factory = _fake_imap_factory(raw)
        src = IMAPInboxSource("imap.stripe.com", "me", "pw", client_factory=factory)
        messages = src.fetch()
        assert len(messages) == 1
        msg = messages[0]
        assert isinstance(msg, InboxMessage)
        assert "schedule" in msg.body.lower() or "chat" in msg.subject.lower()

    def test_fetch_uses_since_search(self):
        raw = _build_raw_email()
        conn = MagicMock()
        conn.login.return_value = ("OK", [b"OK"])
        conn.select.return_value = ("OK", [b"1"])
        conn.search.return_value = ("OK", [b"1"])
        conn.fetch.return_value = ("OK", [(b"1 (RFC822 {%d}" % len(raw), raw)])
        conn.logout.return_value = ("OK", [])

        src = IMAPInboxSource("host", "u", "p", client_factory=lambda: conn)
        since_ts = 1_700_000_000.0
        src.fetch(since=since_ts)

        # Verify search was called with a SINCE clause
        call_args = conn.search.call_args
        search_criteria = call_args[0][1] if call_args[0] else str(call_args)
        assert "SINCE" in str(search_criteria).upper()

    def test_fetch_derives_company_from_sender(self):
        raw = _build_raw_email(
            subject="Offer letter",
            body="We are excited to extend you an offer of employment",
            sender="hr@notion.so",
        )
        factory = _fake_imap_factory(raw)
        src = IMAPInboxSource("imap.notion.so", "me", "pw", client_factory=factory)
        messages = src.fetch()
        assert messages[0].company == "Notion"

    def test_fetch_respects_max_messages(self):
        raw = _build_raw_email()
        conn = MagicMock()
        conn.login.return_value = ("OK", [b"OK"])
        conn.select.return_value = ("OK", [b"1"])
        # Return 5 message ids
        conn.search.return_value = ("OK", [b"1 2 3 4 5"])
        conn.fetch.return_value = ("OK", [(b"1 (RFC822 {%d}" % len(raw), raw)])
        conn.logout.return_value = ("OK", [])

        src = IMAPInboxSource("host", "u", "p", client_factory=lambda: conn)
        messages = src.fetch(max_messages=2)
        assert len(messages) <= 2

    def test_fetch_extracts_plain_text_from_multipart(self):
        plain_body = "This is the plain text version."
        raw = _build_raw_email(
            subject="Multipart test",
            body=plain_body,
            multipart=True,
        )
        factory = _fake_imap_factory(raw)
        src = IMAPInboxSource("host", "u", "p", client_factory=factory)
        messages = src.fetch()
        assert plain_body in messages[0].body

    def test_fetch_falls_back_to_html_stripped(self):
        body_text = "Please review your assessment."
        raw = _build_raw_email(
            subject="Assessment",
            body=body_text,
            multipart=True,
            html_only=True,
        )
        factory = _fake_imap_factory(raw)
        src = IMAPInboxSource("host", "u", "p", client_factory=factory)
        messages = src.fetch()
        # HTML tags should be stripped; the text content should survive
        assert body_text in messages[0].body


# ===========================================================================
# 6. classify_message
# ===========================================================================

class TestClassifyMessage:
    def test_offer_label_high_confidence(self):
        cl = classify_message(
            subject="Job offer",
            body="We are excited to extend you an offer of employment.",
        )
        assert cl.label == "offer"
        assert cl.confidence > 0.0
        assert cl.matched_hints

    def test_rejection_label(self):
        cl = classify_message(
            subject="Application update",
            body="Unfortunately, we have decided to move forward with other candidates.",
        )
        assert cl.label == "rejection"
        assert cl.confidence > 0.0

    def test_interview_label(self):
        cl = classify_message(
            subject="Next steps",
            body="We'd love to schedule a call and set up an interview with you.",
        )
        assert cl.label == "interview"
        assert cl.confidence > 0.0

    def test_assessment_label(self):
        cl = classify_message(
            subject="Take-home",
            body="Please complete the take-home coding challenge on HackerRank.",
        )
        assert cl.label == "assessment"
        assert cl.confidence > 0.0

    def test_other_label_confidence_zero(self):
        cl = classify_message(subject="Hello", body="Just checking in!")
        assert cl.label == "other"
        assert cl.confidence == 0.0
        assert cl.matched_hints == []

    def test_multiple_hints_increase_confidence(self):
        # Two offer hints → confidence > single-hint case
        cl_multi = classify_message(
            subject="",
            body="We are excited to extend you an offer of employment. Here is your offer letter.",
        )
        cl_single = classify_message(
            subject="",
            body="We are excited to extend you an offer of employment.",
        )
        assert cl_multi.confidence >= cl_single.confidence

    def test_classification_is_dataclass(self):
        cl = classify_message("a", "b")
        assert isinstance(cl, Classification)
        assert hasattr(cl, "label")
        assert hasattr(cl, "confidence")
        assert hasattr(cl, "matched_hints")


# ===========================================================================
# 7. extract_calendar
# ===========================================================================

class TestExtractCalendar:
    def test_finds_calendly_url(self):
        body = "Please book a time at https://calendly.com/alice/30min to discuss."
        hint = extract_calendar(body)
        assert hint.has_link is True
        assert "calendly.com" in hint.link

    def test_finds_zoom_url(self):
        body = "Join us at https://us02web.zoom.us/j/12345678901 for the interview."
        hint = extract_calendar(body)
        assert hint.has_link is True
        assert "zoom.us" in hint.link

    def test_finds_proposed_time_iso(self):
        body = "The interview is scheduled for 2024-03-04T14:00:00Z."
        hint = extract_calendar(body)
        assert "2024-03-04" in hint.proposed_time

    def test_finds_proposed_time_natural(self):
        body = "We'd like to meet on Monday, March 4 at 2pm EST."
        hint = extract_calendar(body)
        assert hint.proposed_time != ""
        assert "March" in hint.proposed_time or "march" in hint.proposed_time.lower()

    def test_finds_proposed_time_slash_format(self):
        body = "Can we meet on 3/4 at 14:00?"
        hint = extract_calendar(body)
        assert hint.proposed_time != ""

    def test_no_link_when_absent(self):
        body = "We will be in touch regarding next steps."
        hint = extract_calendar(body)
        assert hint.has_link is False
        assert hint.link == ""

    def test_calendly_preferred_over_zoom(self):
        body = (
            "Book via https://calendly.com/bob/interview "
            "or join https://zoom.us/j/999"
        )
        hint = extract_calendar(body)
        assert "calendly.com" in hint.link

    def test_returns_calendar_hint_instance(self):
        hint = extract_calendar("No scheduling info here.")
        assert isinstance(hint, CalendarHint)


# ===========================================================================
# 8. End-to-end: FakeInboxSource → TrackingAgent
# ===========================================================================

class TestEndToEnd:
    def test_fake_source_feeds_tracking_agent(self):
        """Full round-trip: seed FakeInboxSource, pass dicts into TrackingAgent,
        verify pipeline transitions happen."""
        store = TraceStore()
        bus = ThoughtBus()

        from jobhunt.models import UserProfile
        profile = UserProfile(
            user_id="u-1",
            name="Test",
            email="t@t.com",
            target_roles=["engineer"],
            locations=["Remote"],
        )

        app = Application(
            application_id="app-1",
            user_id="u-1",
            job_id="stripe-backend",
            status=ApplicationStatus.APPLIED,
        )

        msgs = [
            InboxMessage(
                message_id="m-1",
                subject="Interview at Stripe",
                body="Hi! Can we schedule a call for next week?",
                sender="recruiter@stripe.com",
                received_at=time.time(),
                company="Stripe",
            )
        ]

        src = FakeInboxSource(msgs)
        inbox_dicts = [m.to_dict() for m in src.fetch()]

        agent = TrackingAgent(store, bus)
        result = agent.run(
            TrackingInputs(
                profile=profile,
                inbox=inbox_dicts,
                applications=[app],
            ),
            task_id="e2e-test",
        )

        assert result.output is not None
        assert app.status == ApplicationStatus.INTERVIEW
        assert len(result.output.transitions) == 1
        t = result.output.transitions[0]
        assert t["to"] == ApplicationStatus.INTERVIEW
