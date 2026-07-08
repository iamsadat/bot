"""Outbound notifications + webhooks.

Fan out high-value events (new matches, auto-applied, interview detected) to
Slack / Discord / Telegram / generic webhooks (and optional email). Every sink
POSTs via the shared :class:`~jobhunt.submitters.base.Poster` abstraction, so
the offline suite drives them with ``FakePoster`` and never hits the network.

Configured from env via :func:`build_notifier_from_env`; absent config → no
notifier (feature off). Delivery is best-effort: a failing sink never raises.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable, Protocol

logger = logging.getLogger(__name__)

from jobhunt.submitters.base import Poster, UrllibPoster


@dataclass
class NotificationEvent:
    kind: str           # discovered | applied | interview | offer | rejection | test
    title: str
    body: str = ""
    url: str = ""

    def as_text(self) -> str:
        line = f"*{self.title}*" if self.title else ""
        if self.body:
            line += f"\n{self.body}"
        if self.url:
            line += f"\n{self.url}"
        return line.strip()


class Sink(Protocol):
    name: str
    def send(self, event: NotificationEvent) -> bool: ...


class SlackSink:
    name = "slack"
    def __init__(self, webhook_url: str, poster: Poster) -> None:
        self._url, self._poster = webhook_url, poster

    def send(self, event: NotificationEvent) -> bool:
        status, _ = self._poster.post_json(
            self._url, headers={}, body={"text": event.as_text()})
        return 200 <= status < 300


class DiscordSink:
    name = "discord"
    def __init__(self, webhook_url: str, poster: Poster) -> None:
        self._url, self._poster = webhook_url, poster

    def send(self, event: NotificationEvent) -> bool:
        status, _ = self._poster.post_json(
            self._url, headers={}, body={"content": event.as_text()})
        return 200 <= status < 300


class TelegramSink:
    name = "telegram"
    def __init__(self, bot_token: str, chat_id: str, poster: Poster) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id, self._poster = chat_id, poster

    def send(self, event: NotificationEvent) -> bool:
        status, _ = self._poster.post_json(
            self._url, headers={},
            body={"chat_id": self._chat_id, "text": event.as_text(),
                  "parse_mode": "Markdown"})
        return 200 <= status < 300


class WebhookSink:
    """Generic outbound webhook (Zapier / n8n / Make / your own). Posts the
    full structured event so downstream automations can route on ``kind``."""
    name = "webhook"
    def __init__(self, url: str, poster: Poster) -> None:
        self._url, self._poster = url, poster

    def send(self, event: NotificationEvent) -> bool:
        status, _ = self._poster.post_json(
            self._url, headers={},
            body={"kind": event.kind, "title": event.title,
                  "body": event.body, "url": event.url})
        return 200 <= status < 300


class EmailSink:
    """SMTP email sink with an injectable ``send_fn`` for offline tests."""
    name = "email"
    def __init__(
        self, host: str, port: int, user: str, password: str,
        to_addr: str, *, send_fn: Callable[..., None] | None = None,
    ) -> None:
        self._host, self._port = host, port
        self._user, self._password = user, password
        self._to = to_addr
        self._send_fn = send_fn

    def send(self, event: NotificationEvent) -> bool:
        body = f"Subject: JobHunt: {event.title}\n\n{event.body}\n{event.url}".strip()
        if self._send_fn is not None:
            self._send_fn(self._user, self._to, body)
            return True
        import smtplib  # stdlib; only imported on real send
        with smtplib.SMTP(self._host, self._port, timeout=15) as s:
            s.starttls()
            s.login(self._user, self._password)
            s.sendmail(self._user, [self._to], body)
        return True


class Notifier:
    def __init__(self, sinks: list[Sink]) -> None:
        self.sinks = sinks

    def notify(self, event: NotificationEvent) -> int:
        """Fan out to every sink; best-effort. Returns the delivered count."""
        delivered = 0
        for sink in self.sinks:
            try:
                if sink.send(event):
                    delivered += 1
            except Exception:
                logger.warning("notification sink %r failed", type(sink).__name__, exc_info=True)
        return delivered

    def __bool__(self) -> bool:
        return bool(self.sinks)


def build_notifier_from_env(poster: Poster | None = None) -> Notifier | None:
    """Build a Notifier from env. Returns None when nothing is configured."""
    poster = poster or UrllibPoster()
    sinks: list[Sink] = []
    if os.environ.get("JOBHUNT_SLACK_WEBHOOK"):
        sinks.append(SlackSink(os.environ["JOBHUNT_SLACK_WEBHOOK"], poster))
    if os.environ.get("JOBHUNT_DISCORD_WEBHOOK"):
        sinks.append(DiscordSink(os.environ["JOBHUNT_DISCORD_WEBHOOK"], poster))
    if os.environ.get("JOBHUNT_TELEGRAM_BOT_TOKEN") and os.environ.get("JOBHUNT_TELEGRAM_CHAT_ID"):
        sinks.append(TelegramSink(
            os.environ["JOBHUNT_TELEGRAM_BOT_TOKEN"],
            os.environ["JOBHUNT_TELEGRAM_CHAT_ID"], poster))
    for url in (os.environ.get("JOBHUNT_WEBHOOK_URLS", "")).split(","):
        if url.strip():
            sinks.append(WebhookSink(url.strip(), poster))
    if os.environ.get("JOBHUNT_SMTP_HOST") and os.environ.get("JOBHUNT_NOTIFY_EMAIL"):
        sinks.append(EmailSink(
            os.environ["JOBHUNT_SMTP_HOST"], int(os.environ.get("JOBHUNT_SMTP_PORT", "587")),
            os.environ.get("JOBHUNT_SMTP_USER", ""), os.environ.get("JOBHUNT_SMTP_PASSWORD", ""),
            os.environ["JOBHUNT_NOTIFY_EMAIL"]))
    return Notifier(sinks) if sinks else None
