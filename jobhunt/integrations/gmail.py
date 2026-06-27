"""Gmail REST integration — a drop-in :class:`InboxSource`.

``GmailInboxSource`` satisfies the same ``InboxSource``-compatible surface
as :class:`jobhunt.inbox.sources.IMAPInboxSource` (a ``fetch`` method
returning ``InboxMessage`` instances, plus ``name``), so the tracking
pipeline can swap IMAP for Gmail without any other code changes. It also
adds ``mark_read`` for parity with IMAP's read-state semantics.

All network access goes through an injectable
:class:`jobhunt.integrations.google_auth.Transport`; no Google client
libraries and no real OAuth are required, so this module is fully
unit-testable offline.
"""

from __future__ import annotations

import base64
import binascii
import re

from jobhunt.inbox.sources import InboxMessage, _company_from_email
from jobhunt.integrations.google_auth import TokenProvider, Transport

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


# ---------------------------------------------------------------------------
# Base64url helpers
# ---------------------------------------------------------------------------

def _b64url_decode(data: str) -> bytes:
    """Decode base64url (Gmail's body encoding), tolerating missing padding."""
    if not data:
        return b""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError):
        return b""


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _decode_part_body(part: dict) -> str:
    """Decode the base64url ``body.data`` of a single message part."""
    data = (part.get("body") or {}).get("data", "")
    raw = _b64url_decode(data)
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace")


def _extract_body_from_payload(payload: dict) -> str:
    """Walk a (possibly multipart) Gmail ``payload`` for the best text body.

    Mirrors ``jobhunt.inbox.sources._extract_body``: prefers ``text/plain``,
    falls back to a tag-stripped ``text/html``, walking nested parts
    (Gmail multipart/alternative and multipart/mixed are both nestable).
    """
    plain: str | None = None
    html: str | None = None

    def _walk(node: dict) -> None:
        nonlocal plain, html
        mime_type = node.get("mimeType", "")
        parts = node.get("parts")
        if parts:
            for part in parts:
                _walk(part)
            return
        if mime_type == "text/plain" and plain is None:
            text = _decode_part_body(node)
            if text:
                plain = text
        elif mime_type == "text/html" and html is None:
            text = _decode_part_body(node)
            if text:
                html = text
        elif plain is None and html is None and node.get("body", {}).get("data"):
            # Single-part message without an explicit text/* mimeType match
            # (rare, but be permissive) — treat as plain.
            plain = _decode_part_body(node)

    _walk(payload)

    if plain is not None:
        return plain
    if html is not None:
        return _HTML_TAG_RE.sub(" ", html)
    return ""


def _header(headers: list[dict], name: str) -> str:
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value", "") or ""
    return ""


def _parse_internal_date(value: str | None) -> float:
    """Gmail's ``internalDate`` is milliseconds since epoch, as a string."""
    if not value:
        return 0.0
    try:
        return int(value) / 1000.0
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# GmailInboxSource
# ---------------------------------------------------------------------------

class GmailInboxSource:
    """Gmail-backed :class:`~jobhunt.inbox.sources.InboxSource`.

    Constructed with a :class:`TokenProvider` and :class:`Transport`; makes
    no network calls until ``fetch``/``mark_read`` are invoked.
    """

    name = "gmail"

    def __init__(
        self,
        token_provider: TokenProvider,
        transport: Transport,
        *,
        user_id: str = "me",
        label_ids: list[str] | None = None,
    ) -> None:
        self._tokens = token_provider
        self._transport = transport
        self._user_id = user_id
        self._label_ids = label_ids if label_ids is not None else ["INBOX"]

    # ------------------------------------------------------------------
    # Public API — InboxSource-compatible
    # ------------------------------------------------------------------

    def fetch(
        self, *, since: float = 0.0, max_messages: int = 50
    ) -> list[InboxMessage]:
        message_ids = self._list_message_ids(max_messages=max_messages)
        messages: list[InboxMessage] = []
        for mid in message_ids:
            msg = self._get_message(mid)
            if msg is not None and msg.received_at > since:
                messages.append(msg)
        return messages[:max_messages]

    def mark_read(self, message_id: str) -> None:
        """Remove the ``UNREAD`` label from *message_id* (IMAP-parity op)."""
        status, _ = self._request(
            "POST",
            f"{GMAIL_API_BASE}/messages/{message_id}/modify",
            body={"removeLabelIds": ["UNREAD"]},
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _list_message_ids(self, *, max_messages: int) -> list[str]:
        params = f"maxResults={max_messages}"
        if self._label_ids:
            for label in self._label_ids:
                params += f"&labelIds={label}"
        url = f"{GMAIL_API_BASE}/messages?{params}"
        _, payload = self._request("GET", url)
        return [m["id"] for m in payload.get("messages", []) if "id" in m]

    def _get_message(self, message_id: str) -> InboxMessage | None:
        url = f"{GMAIL_API_BASE}/messages/{message_id}?format=full"
        _, payload = self._request("GET", url)
        if not payload:
            return None

        payload_node = payload.get("payload", {}) or {}
        headers = payload_node.get("headers", []) or []

        subject = _header(headers, "Subject")
        sender = _header(headers, "From")
        gmail_message_id = payload.get("id", message_id)

        received_at = _parse_internal_date(payload.get("internalDate"))
        if received_at == 0.0:
            received_at = self._parse_date_header(_header(headers, "Date"))

        body = _extract_body_from_payload(payload_node)
        if not body:
            body = payload.get("snippet", "") or ""

        company = _company_from_email(sender)

        return InboxMessage(
            message_id=gmail_message_id,
            subject=subject,
            body=body,
            sender=sender,
            received_at=received_at,
            company=company,
        )

    @staticmethod
    def _parse_date_header(date_str: str) -> float:
        if not date_str:
            return 0.0
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(date_str).timestamp()
        except Exception:
            return 0.0

    def _request(
        self, method: str, url: str, *, body: dict | None = None
    ) -> tuple[int, dict]:
        token = self._tokens.get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        return self._transport.request(method, url, headers=headers, body=body)
