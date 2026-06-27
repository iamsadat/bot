"""Inbox source abstractions for the JobHunt tracking pipeline.

Provides:
- ``InboxMessage``      — the canonical message dataclass
- ``InboxSource``       — Protocol that sources must satisfy
- ``FakeInboxSource``   — test double seeded with messages
- ``IMAPInboxSource``   — real IMAP4_SSL source (lazy connection)
"""

from __future__ import annotations

import email
import email.message
import imaplib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _company_from_email(addr: str) -> str:
    """Derive a human-readable company name from an e-mail address.

    Examples:
        ``noreply@stripe.com``          → ``Stripe``
        ``jobs@careers.acme-corp.io``  → ``Acme Corp``
    """
    # Extract the domain portion after @
    match = re.search(r"@([\w.%-]+)", addr)
    if not match:
        return ""
    domain = match.group(1)

    # Strip common subdomain prefixes that are not the brand name
    _SUBDOMAIN_NOISE = {"careers", "mail", "email", "jobs", "hiring", "noreply",
                        "no-reply", "hr", "recruiting", "talent"}
    parts = domain.split(".")
    # Drop TLD(s) — last 1 or 2 segments (e.g. "co.uk" → drop 2, ".com" → drop 1)
    if len(parts) >= 3 and len(parts[-2]) <= 3:
        # two-part TLD like co.uk / com.au
        brand_parts = parts[:-2]
    else:
        brand_parts = parts[:-1]

    # Filter out noise subdomains, keep what's left
    brand_parts = [p for p in brand_parts if p.lower() not in _SUBDOMAIN_NOISE]
    if not brand_parts:
        # Nothing meaningful left — fall back to the first real label
        brand_parts = [parts[0]] if parts else []

    # Take the last remaining part as the brand (e.g. "acme-corp")
    brand = brand_parts[-1] if brand_parts else ""

    # Convert hyphens/underscores to spaces and title-case each word
    brand = re.sub(r"[-_]+", " ", brand).strip()
    return brand.title()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class InboxMessage:
    """Canonical representation of one inbox message."""

    message_id: str
    subject: str
    body: str
    sender: str
    received_at: float                  # unix timestamp
    company: str = ""                   # derived from sender domain when present

    def to_dict(self) -> dict:
        """Return a dict compatible with ``TrackingInputs.inbox`` shape."""
        return {
            "subject": self.subject,
            "body": self.body,
            "company": self.company,
        }


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class InboxSource(Protocol):
    name: str

    def fetch(
        self, *, since: float = 0.0, max_messages: int = 50
    ) -> list[InboxMessage]:
        ...


# ---------------------------------------------------------------------------
# Fake / test double
# ---------------------------------------------------------------------------

class FakeInboxSource:
    """Test double.  Seed with a list of ``InboxMessage``; ``fetch`` returns
    them filtered by ``since`` and capped at ``max_messages``."""

    name = "fake"

    def __init__(self, messages: list[InboxMessage]) -> None:
        self._messages = list(messages)

    def fetch(
        self, *, since: float = 0.0, max_messages: int = 50
    ) -> list[InboxMessage]:
        filtered = [m for m in self._messages if m.received_at > since]
        return filtered[:max_messages]


# ---------------------------------------------------------------------------
# Real IMAP source
# ---------------------------------------------------------------------------

def _default_imap_factory(host: str, port: int):
    """Create a real ``imaplib.IMAP4_SSL`` connection."""
    return imaplib.IMAP4_SSL(host, port)


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _extract_body(msg: email.message.Message) -> str:
    """Return the best plain-text body from a (possibly multipart) message."""
    plain: str | None = None
    html: str | None = None

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and plain is None:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    plain = payload.decode(charset, errors="replace")
            elif ct == "text/html" and html is None:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            ct = msg.get_content_type()
            if ct == "text/html":
                html = text
            else:
                plain = text

    if plain is not None:
        return plain
    if html is not None:
        # Minimal HTML strip
        return _HTML_TAG_RE.sub(" ", html)
    return ""


class IMAPInboxSource:
    """IMAP4_SSL inbox source.

    Connection is lazy — the constructor never opens a socket.  Pass a
    ``client_factory`` callable to inject a fake in tests; the default
    creates ``imaplib.IMAP4_SSL(host, port)``.
    """

    name = "imap"

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        mailbox: str = "INBOX",
        port: int = 993,
        client_factory: Callable | None = None,
    ) -> None:
        self._host = host
        self._username = username
        self._password = password
        self._mailbox = mailbox
        self._port = port
        self._factory = client_factory or (
            lambda: _default_imap_factory(self._host, self._port)
        )
        self._conn = None  # lazy

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch(
        self, *, since: float = 0.0, max_messages: int = 50
    ) -> list[InboxMessage]:
        conn = self._connect()
        try:
            conn.select(self._mailbox)
            ids = self._search(conn, since)
            # Newest-first: reverse so we take the most recent up to max
            ids = list(reversed(ids))[:max_messages]
            return [m for m in (self._fetch_one(conn, uid) for uid in ids) if m]
        finally:
            try:
                conn.logout()
            except Exception:
                pass
            self._conn = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _connect(self):
        conn = self._factory()
        conn.login(self._username, self._password)
        return conn

    def _search(self, conn, since: float) -> list[bytes]:
        if since > 0.0:
            dt = datetime.fromtimestamp(since, tz=timezone.utc)
            date_str = dt.strftime("%d-%b-%Y")
            _, data = conn.search(None, f'(SINCE "{date_str}")')
        else:
            _, data = conn.search(None, "ALL")
        raw = data[0] if data else b""
        if not raw:
            return []
        return raw.split()

    def _fetch_one(self, conn, uid: bytes) -> InboxMessage | None:
        _, data = conn.fetch(uid, "(RFC822)")
        if not data or data[0] is None:
            return None
        raw_bytes = data[0][1] if isinstance(data[0], tuple) else data[0]
        if not isinstance(raw_bytes, bytes):
            return None
        msg = email.message_from_bytes(raw_bytes)

        subject = msg.get("Subject", "") or ""
        sender = msg.get("From", "") or ""
        msg_id = msg.get("Message-ID", uid.decode()) or uid.decode()

        # Parse received date from Date header
        date_header = msg.get("Date", "")
        received_at = self._parse_date(date_header)

        body = _extract_body(msg)
        company = _company_from_email(sender)

        return InboxMessage(
            message_id=msg_id,
            subject=subject,
            body=body,
            sender=sender,
            received_at=received_at,
            company=company,
        )

    @staticmethod
    def _parse_date(date_str: str) -> float:
        """Parse an RFC 2822 date string to unix timestamp; fall back to now."""
        if not date_str:
            return time.time()
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(date_str).timestamp()
        except Exception:
            return time.time()
