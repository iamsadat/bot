"""Billing rails: Stripe Checkout + webhook verification.

JobHunt has no pricing decided yet — this module is pure scaffolding so
that when a real paying customer shows up, billing can be turned on in
hours: set three env vars, point a Stripe webhook at ``/api/billing/webhook``,
done. Off by default, like every other integration in this codebase (Hunter,
Gmail, Adzuna, ...) — see ``build_contact_finder_from_env()``,
``build_salary_client_from_env()``.

Stripe's REST API is plain HTTPS + form-encoded POST bodies + Bearer auth,
so no ``stripe`` pip package is needed. Reuses the ``Poster`` protocol from
``jobhunt/submitters/base.py`` (the existing injectable POST-with-form-body
abstraction used for offline testing) rather than inventing a new one.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

from jobhunt.submitters.base import Poster, UrllibPoster

_CHECKOUT_URL = "https://api.stripe.com/v1/checkout/sessions"


class StripeClient:
    """Thin wrapper over the Stripe REST API. Stdlib-only, offline-testable."""

    def __init__(self, secret_key: str, poster: Poster | None = None) -> None:
        self._key = secret_key
        self._poster = poster or UrllibPoster()

    def create_checkout_session(
        self,
        ws_id: str,
        email: str | None,
        price_id: str,
        success_url: str,
        cancel_url: str,
    ) -> dict:
        """Create a Stripe Checkout session for a subscription purchase.

        Returns the parsed JSON response (includes ``id`` and ``url`` on
        success). Raises ``RuntimeError`` on a non-2xx response.
        """
        form = {
            "mode": "subscription",
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": ws_id,
        }
        if email:
            form["customer_email"] = email
        body = urlencode(form).encode()
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        status, payload = self._poster.post_json(_CHECKOUT_URL, headers=headers, body=body)
        if status >= 300:
            raise RuntimeError(f"Stripe checkout session creation failed ({status}): {payload}")
        return payload


def verify_webhook_signature(
    payload: bytes,
    sig_header: str,
    webhook_secret: str,
    *,
    tolerance_seconds: int = 300,
    now: float | None = None,
) -> bool:
    """Verify a Stripe ``Stripe-Signature`` header per Stripe's documented scheme.

    The header looks like ``t=<timestamp>,v1=<sig>[,v1=<sig2>...]`` — there
    may be multiple ``v1=`` entries during webhook-secret rotation; any one
    matching is accepted. Rejects payloads whose timestamp is more than
    ``tolerance_seconds`` old (replay protection); ``now`` is injectable for
    deterministic tests.
    """
    if not sig_header or not webhook_secret:
        return False

    timestamp: str | None = None
    signatures: list[str] = []
    for part in sig_header.split(","):
        part = part.strip()
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)

    if timestamp is None or not signatures:
        return False

    try:
        ts = int(timestamp)
    except ValueError:
        return False

    now = time.time() if now is None else now
    if abs(now - ts) > tolerance_seconds:
        return False

    signed_payload = f"{timestamp}.".encode() + payload
    expected = hmac.new(
        webhook_secret.encode(), signed_payload, hashlib.sha256,
    ).hexdigest()

    return any(hmac.compare_digest(expected, sig) for sig in signatures)


def build_stripe_client_from_env(poster: Poster | None = None) -> StripeClient | None:
    """Build a ``StripeClient`` from ``STRIPE_SECRET_KEY``, or ``None`` if unset."""
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        return None
    return StripeClient(key, poster)
